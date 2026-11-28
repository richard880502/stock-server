"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { parseProposalParam, PAPER_ACCOUNT_KEY, prefillFromProposal, STATUS_LABEL as PROPOSAL_STATUS } from "@/features/proposals/proposal-math";
import { ProposalsPanel } from "@/features/proposals/proposals-panel";
import { useProposalLookup } from "@/features/proposals/use-proposal-lookup";
import type { Proposal } from "@/lib/api/proposals";
import { useDashboard } from "@/lib/dashboard-context";
import { useLiveQuotes } from "@/lib/live-quotes";
import { readStorage, writeStorage } from "@/lib/safe-storage";
import { AccountSettingsForm, NewAccountForm, type SettingsFocus } from "./account-forms";
import { AccountHeader, AccountHeaderSkeleton } from "./account-header";
import { EquityCard } from "./equity-card";
import { OrderTicket, type TicketPrefill } from "./order-ticket";
import "@/features/proposals/proposals.css";
import { OrdersPanel } from "./orders-panel";
import { applyLivePrices, type EquityRange, maxSellLots, parseDeepLink, RANGE_DAYS } from "./paper-math";
import { PaperDialog, SIM_DISCLAIMER, SimBadge } from "./paper-ui";
import { PositionsTable } from "./positions-table";
import { usePolled } from "./use-paper-data";

const ACCOUNT_KEY = PAPER_ACCOUNT_KEY;
const NO_SYMBOLS: string[] = [];

type Dialog =
  | null
  | { kind: "new" }
  | { kind: "settings"; focus: SettingsFocus }
  | { kind: "reset"; step: 1 | 2 }
  | { kind: "archive" };

/** 模擬交易: header strip, equity curve, positions, orders, and the order ticket. */
export function PaperPage() {
  const search = useSearchParams();
  const router = useRouter();
  const { recentSymbols } = useDashboard();

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
  const [proposalsToken, setProposalsToken] = useState(0);
  const proposalParam = parseProposalParam(search);
  const appliedProposal = useRef<string | null>(null);
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
  const fillFromProposal = useCallback((proposal: Proposal) => {
    const fill = prefillFromProposal(proposal);
    if (!fill) return;
    setPrefill((previous) => ({ nonce: (previous?.nonce ?? 0) + 1, ...fill }));
    setSheetOpen(true);
  }, []);

  // `?proposal=<id>`: switch to the account that owns it, then fill the ticket once.
  const lookup = useProposalLookup(proposalParam, list.length ? list : null, accountId, (proposal) => {
    if (appliedProposal.current === proposal.id) return;
    appliedProposal.current = proposal.id;
    if (proposal.account_id && proposal.account_id !== accountId) choose(proposal.account_id);
    if (proposal.status === "pending") fillFromProposal(proposal);
  });

  const proposalNotice = (() => {
    if (!proposalParam || !lookup) return null;
    if (lookup.phase === "missing") return "找不到這份 AI 提案，可能已被刪除，或不屬於你的帳戶。";
    if (lookup.phase === "error") return "暫時無法讀取這份 AI 提案，請稍後再試。";
    if (lookup.phase !== "found") return null;
    const proposal = lookup.proposal;
    if (proposal.status !== "pending") {
      const label = typeof proposal.status === "string" ? PROPOSAL_STATUS[proposal.status as keyof typeof PROPOSAL_STATUS] : null;
      return `這份 AI 提案${label ? `狀態為「${label}」` : "已不是待確認狀態"}，不能再帶入委託。`;
    }
    return prefillFromProposal(proposal) ? null : "這份 AI 提案沒有可帶入的委託。";
  })();
  const clearProposalParam = () => {
    if (proposalParam) router.replace("/paper");
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
      {proposalNotice && (
        <div className="notice" role="status">
          <div className="notice-body">
            <p>{proposalNotice}</p>
          </div>
          <div className="notice-actions">
            <button type="button" className="button small" onClick={clearProposalParam}>
              關閉
            </button>
          </div>
        </div>
      )}
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
          />
          {accountId && (
            <ProposalsPanel accountId={accountId} refreshToken={proposalsToken} onUse={fillFromProposal} />
          )}
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
            onProposalSubmitted={(consumed) => {
              setProposalsToken((n) => n + 1);
              if (consumed) clearProposalParam();
            }}
            onViewProposal={() => setSheetOpen(false)}
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
