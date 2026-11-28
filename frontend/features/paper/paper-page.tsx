"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/card";
import { ErrorBanner } from "@/components/error-banner";
import {
  archiveAccount,
  createAccount,
  getAccount,
  getEquity,
  getRules,
  listAccounts,
  type NewAccount,
  type AccountPatch,
  type OrderSide,
  type Position,
  resetAccount,
  updateAccount,
} from "@/lib/api/paper";
import { useDashboard } from "@/lib/dashboard-context";
import { useLiveQuotes } from "@/lib/live-quotes";
import { readStorage, writeStorage } from "@/lib/safe-storage";
import { AccountSettingsForm, NewAccountForm, type SettingsFocus } from "./account-forms";
import { AccountHeader, AccountHeaderSkeleton } from "./account-header";
import { EquityCard } from "./equity-card";
import { OrderTicket, type TicketPrefill } from "./order-ticket";
import { OrdersPanel } from "./orders-panel";
import { applyLivePrices, type EquityRange, maxSellLots, parseDeepLink, RANGE_DAYS } from "./paper-math";
import { PaperDialog, SIM_DISCLAIMER, SimBadge } from "./paper-ui";
import { PositionsTable } from "./positions-table";
import { usePolled } from "./use-paper-data";

const ACCOUNT_KEY = "quant_signal_paper_account";
const NO_SYMBOLS: string[] = [];

type Dialog =
  | null
  | { kind: "new" }
  | { kind: "settings"; focus: SettingsFocus }
  | { kind: "reset"; step: 1 | 2 }
  | { kind: "archive" };

/** 模擬交易: header strip, equity curve, positions, orders, and the order ticket. */
export function PaperPage() {
  const router = useRouter();
  const search = useSearchParams();
  const { refresh, recentSymbols } = useDashboard();

  const accounts = usePolled("accounts", (signal) => listAccounts({ signal }));
  const rules = usePolled("rules", (signal) => getRules({ signal }));
  const [chosen, setChosen] = useState<string | null>(() => readStorage(ACCOUNT_KEY));
  const list = accounts.data?.accounts ?? [];
  const account = list.find((item) => item.id === chosen) ?? list[0] ?? null;
  const accountId = account?.id ?? null;

  const detail = usePolled(accountId ? `detail|${accountId}` : null, (signal) => getAccount(accountId as string, { signal }), 10_000);
  const [range, setRange] = useState<EquityRange>("3m");
  const equity = usePolled(
    accountId ? `equity|${accountId}|${range}` : null,
    (signal) => getEquity(accountId as string, RANGE_DAYS[range], { signal }),
    60_000,
  );

  const [ticketSymbol, setTicketSymbol] = useState<string | null>(null);
  const [prefill, setPrefill] = useState<TicketPrefill | null>(() => {
    const link = parseDeepLink(search);
    return link.symbol ? { nonce: 1, symbol: link.symbol, side: link.side ?? "buy", lots: link.lots ?? undefined } : null;
  });
  const [sheetOpen, setSheetOpen] = useState(false);
  const [ordersToken, setOrdersToken] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const detailData = detail.data && detail.data.id === accountId ? detail.data : null;
  const symbols = useMemo(() => {
    const set = new Set<string>(detailData?.positions.map((p) => p.symbol) ?? []);
    if (ticketSymbol) set.add(ticketSymbol);
    return set.size ? [...set] : NO_SYMBOLS;
  }, [detailData, ticketSymbol]);
  const { quotes } = useLiveQuotes(symbols);
  const live = useMemo(() => (detailData ? applyLivePrices(detailData, quotes) : null), [detailData, quotes]);

  const names = useMemo(() => {
    const map = new Map<string, string>();
    recentSymbols.forEach((item) => item.name && map.set(item.symbol, item.name));
    detailData?.positions.forEach((p) => map.set(p.symbol, p.name));
    return map;
  }, [recentSymbols, detailData]);

  const { reload: reloadAccounts } = accounts;
  const { reload: reloadDetail } = detail;
  const { reload: reloadEquity } = equity;
  const refreshMoney = useCallback(() => {
    reloadDetail();
    reloadAccounts();
    reloadEquity();
  }, [reloadDetail, reloadAccounts, reloadEquity]);
  const afterOrderChange = useCallback(() => {
    setOrdersToken((n) => n + 1);
    refreshMoney();
  }, [refreshMoney]);

  // Escape closes the phone order sheet.
  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSheetOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sheetOpen]);

  const choose = (id: string) => {
    setChosen(id);
    writeStorage(ACCOUNT_KEY, id);
  };
  const closeDialog = () => {
    setDialog(null);
    setFormError(null);
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setFormError(null);
    try {
      await action();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "操作失敗");
    } finally {
      setBusy(false);
    }
  };

  const create = (body: NewAccount) =>
    run(async () => {
      const created = await createAccount(body);
      choose(created.id);
      accounts.reload();
      closeDialog();
    });
  const patch = (changes: AccountPatch) =>
    run(async () => {
      if (!accountId) return;
      await updateAccount(accountId, changes);
      refreshMoney();
      closeDialog();
    });
  const reset = () =>
    run(async () => {
      if (!accountId) return;
      await resetAccount(accountId);
      afterOrderChange();
      closeDialog();
    });
  const archive = () =>
    run(async () => {
      if (!accountId) return;
      await archiveAccount(accountId);
      setChosen(null);
      writeStorage(ACCOUNT_KEY, null);
      accounts.reload();
      closeDialog();
    });

  const trade = (position: Position, side: OrderSide) => {
    setPrefill((previous) => ({
      nonce: (previous?.nonce ?? 0) + 1,
      symbol: position.symbol,
      name: position.name,
      side,
      lots: side === "sell" ? Math.max(1, maxSellLots(position)) : 1,
    }));
    setSheetOpen(true);
  };
  const openSignal = (symbol: string) => {
    refresh({ symbol });
    router.push("/");
  };

  /* ---- Page-level states ------------------------------------------------------------------ */

  if (accounts.error && !accounts.data) {
    if (accounts.error.status === 403) {
      return (
        <div className="paper-page">
          <Card className="paper-gate">
            <SimBadge />
            <h1>模擬交易需要以 Google 帳戶登入</h1>
            <p>使用 API 金鑰或密碼登入時無法使用模擬帳戶。請登出後改用 Google 帳戶登入。</p>
          </Card>
        </div>
      );
    }
    return (
      <div className="paper-page">
        <h1 className="paper-title">模擬交易</h1>
        <ErrorBanner message={accounts.error.message} onDismiss={() => {}} onRetry={accounts.reload} />
      </div>
    );
  }

  if (!accounts.data) {
    return (
      <div className="paper-page" aria-busy="true">
        <AccountHeaderSkeleton />
        <span className="skeleton block" style={{ height: "16rem" }} />
      </div>
    );
  }

  if (!account) {
    return (
      <div className="paper-page">
        <Card className="paper-first-run">
          <div className="paper-first-head">
            <h1>開始模擬交易</h1>
            <SimBadge />
          </div>
          <p>
            用虛擬資金，照著真實的即時行情練習買賣台股，看看你的想法實際會賺還是賠。
            {SIM_DISCLAIMER}，也不會動到你任何真實的錢。
          </p>
          <NewAccountForm onSubmit={create} busy={busy} error={formError} />
        </Card>
      </div>
    );
  }

  const openOrder = () => setSheetOpen(true);
  return (
    <div className="paper-page">
      <AccountHeader
        accounts={list}
        maxAccounts={accounts.data.max_accounts}
        account={account}
        detail={live}
        stale={detail.stale || (detail.loading && !live)}
        actions={{
          onSelect: choose,
          onNew: () => setDialog({ kind: "new" }),
          onSettings: (focus) => setDialog({ kind: "settings", focus }),
          onReset: () => setDialog({ kind: "reset", step: 1 }),
          onArchive: () => setDialog({ kind: "archive" }),
        }}
      />
      {detail.error && !live && <ErrorBanner message={detail.error.message} onDismiss={() => {}} onRetry={detail.reload} />}

      <div className="paper-layout">
        <div className="paper-main">
          <EquityCard
            points={equity.data}
            loading={equity.loading}
            stale={equity.stale}
            range={range}
            onRange={setRange}
          />
          {equity.error && !equity.data && (
            <ErrorBanner message={equity.error.message} onDismiss={() => {}} onRetry={equity.reload} />
          )}
          <PositionsTable
            positions={live?.positions ?? null}
            loading={detail.loading}
            stale={detail.stale}
            onTrade={trade}
            onOpenSignal={openSignal}
          />
          <OrdersPanel
            accountId={account.id}
            workingHint={live?.working_orders ?? account.working_orders}
            refreshToken={ordersToken}
            names={names}
            onWorkingChanged={refreshMoney}
            onCancelled={afterOrderChange}
          />
        </div>

        <aside
          className="paper-ticket-col"
          data-open={sheetOpen}
          aria-label="下單區"
        >
          <div className="paper-sheet-bar">
            <button type="button" className="button small" onClick={() => setSheetOpen(false)}>
              收合
            </button>
          </div>
          <OrderTicket
            account={live ?? account}
            positions={live?.positions ?? []}
            rules={rules.data}
            rulesLoading={rules.loading}
            rulesError={rules.error?.message ?? null}
            prefill={prefill}
            quote={ticketSymbol ? quotes.get(ticketSymbol.toUpperCase()) : undefined}
            recents={recentSymbols}
            onSymbolChange={setTicketSymbol}
            onPlaced={afterOrderChange}
          />
        </aside>
        {sheetOpen && <div className="paper-scrim" onClick={() => setSheetOpen(false)} aria-hidden="true" />}
      </div>

      <button type="button" className="button primary paper-fab" onClick={openOrder} aria-expanded={sheetOpen}>
        下單
      </button>

      <PaperDialog open={dialog?.kind === "new"} onClose={closeDialog} title="新增模擬帳戶">
        <NewAccountForm
          onSubmit={create}
          busy={busy}
          error={formError}
          submitLabel="建立帳戶"
        />
      </PaperDialog>

      <PaperDialog open={dialog?.kind === "settings"} onClose={closeDialog} title="帳戶設定">
        {dialog?.kind === "settings" && (
          <AccountSettingsForm
            account={account}
            focus={dialog.focus}
            busy={busy}
            error={formError}
            onSubmit={patch}
            onCancel={closeDialog}
          />
        )}
      </PaperDialog>

      <PaperDialog
        open={dialog?.kind === "reset"}
        onClose={closeDialog}
        title={dialog?.kind === "reset" && dialog.step === 2 ? "最後確認：重設帳戶" : "重設帳戶"}
      >
        {dialog?.kind === "reset" && (
          <div className="paper-form">
            {dialog.step === 1 ? (
              <>
                <p>重設「{account.name}」會：</p>
                <ul className="paper-rule-list">
                  <li>取消所有委託中的單</li>
                  <li>清除目前持股，並把現金退回初始資金 NT$ {account.initial_cash.toLocaleString("zh-TW")}</li>
                  <li>權益曲線與損益從頭開始計算；舊的成交紀錄會封存，不再計入績效</li>
                </ul>
                <div className="paper-actions">
                  <button type="button" className="button" onClick={closeDialog}>取消</button>
                  <button type="button" className="button danger" onClick={() => setDialog({ kind: "reset", step: 2 })}>
                    我了解，繼續
                  </button>
                </div>
              </>
            ) : (
              <>
                <p>
                  這個動作無法復原。確定要重設「{account.name}」嗎？{SIM_DISCLAIMER}，重設只影響模擬資料。
                </p>
                {formError && <p className="inline-error" role="alert">{formError}</p>}
                <div className="paper-actions">
                  <button type="button" className="button" onClick={closeDialog} disabled={busy}>取消</button>
                  <button type="button" className="button danger" onClick={reset} disabled={busy} autoFocus>
                    {busy ? "重設中…" : "確認重設"}
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </PaperDialog>

      <PaperDialog open={dialog?.kind === "archive"} onClose={closeDialog} title="封存帳戶">
        <div className="paper-form">
          <p>
            封存「{account.name}」後，它會從帳戶清單消失，你就不能再對它下單。
            {account.positions_count > 0 || account.working_orders > 0
              ? "目前還有持股或委託中的單，它們會一併停止更新。"
              : ""}
          </p>
          {formError && <p className="inline-error" role="alert">{formError}</p>}
          <div className="paper-actions">
            <button type="button" className="button" onClick={closeDialog} disabled={busy}>取消</button>
            <button type="button" className="button danger" onClick={archive} disabled={busy}>
              {busy ? "封存中…" : "確認封存"}
            </button>
          </div>
        </div>
      </PaperDialog>
    </div>
  );
}
