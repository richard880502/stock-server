"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/card";
import { EmptyState } from "@/components/empty-state";
import { SymbolCombobox } from "@/components/symbol-combobox";
import { AlertIcon, InfoIcon, StarIcon, XIcon } from "@/components/icons";
import { JobStatusChip } from "@/components/status-chip";
import type { WatchlistItem } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import { formatPrice, shortDate } from "@/lib/format";
import { useWatchlist, WATCHLIST_COVERAGE_NOTE } from "@/lib/use-watchlist";
import { Lots, PctChange } from "@/features/stock/flow-format";

const marketLabel = { TW: "上市", TWO: "上櫃" } as const;

/** Re-fetch statuses when the page opens, unless the list was loaded just now. */
const STALE_AFTER_MS = 20_000;

function ListSkeleton() {
  return (
    <Card aria-label="正在載入自選股">
      <div aria-busy="true" className="board-skeleton">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="skeleton block" style={{ height: 40 }} />
        ))}
      </div>
    </Card>
  );
}

/** Where the institutional columns stand: one sentence for the whole list. */
function dataFootnote(items: WatchlistItem[]): string {
  const quotes = items.map((i) => i.data_through).filter((d): d is string => d !== null);
  const flows = items.map((i) => i.flows_through).filter((d): d is string => d !== null);
  const parts: string[] = [];
  if (quotes.length > 0) parts.push(`行情資料截至 ${shortDate(quotes.reduce((a, b) => (b > a ? b : a)))}`);
  if (flows.length === 0) {
    parts.push("法人資料尚未公布");
  } else {
    const first = flows.reduce((a, b) => (b < a ? b : a));
    const last = flows.reduce((a, b) => (b > a ? b : a));
    parts.push(
      first === last
        ? `法人資料更新至 ${shortDate(last)}`
        : `法人資料更新至 ${shortDate(first)}–${shortDate(last)}（各檔報表公布時間不同，以各檔最新已公布的交易日計算）`,
    );
  }
  return parts.join(" · ");
}

function Row({
  item,
  removing,
  onOpen,
  onRemove,
}: {
  item: WatchlistItem;
  removing: boolean;
  onOpen: (symbol: string) => void;
  onRemove: (item: WatchlistItem) => void;
}) {
  const code = item.symbol.replace(/\.TWO?$/, "");
  return (
    <tr className="watch-row" onClick={() => onOpen(item.symbol)}>
      <th scope="row" className="col-stock">
        {/* The click bubbles to the row, so Enter / Space on the button works too. */}
        <button
          type="button"
          className="stock-link"
          aria-label={`${item.name || code}（${code}），查看今日訊號`}
        >
          <span className="stock-code num">{code}</span>
          <span className="stock-name">{item.name}</span>
          <span className="market-tag">{marketLabel[item.market]}</span>
        </button>
      </th>
      <td className="col-close num" data-label="收盤">
        {item.close === null ? "—" : formatPrice(item.close)}
      </td>
      <td className="col-change num" data-label="當日漲跌">
        <PctChange value={item.day_change_pct} />
      </td>
      <td className="col-flow1 num" data-label="外資 1 日（張）">
        <Lots value={item.foreign_net_1d_lots} empty="—" />
      </td>
      <td className="col-flow5 num" data-label="三大法人 5 日（張）">
        <Lots value={item.total_net_5d_lots} empty="—" />
      </td>
      <td className="col-status">
        {item.full_analysis === "ready" ? (
          <JobStatusChip status="completed">可完整分析</JobStatusChip>
        ) : (
          <JobStatusChip status="running">分析準備中</JobStatusChip>
        )}
      </td>
      <td className="col-remove">
        <button
          type="button"
          className="icon-button small"
          aria-label={`將 ${item.name || code} 移出自選股`}
          title="移出自選股"
          aria-disabled={removing || undefined}
          onClick={(event) => {
            event.stopPropagation();
            if (!removing) onRemove(item);
          }}
        >
          <XIcon size={16} />
        </button>
      </td>
    </tr>
  );
}

export function WatchlistView() {
  const { refresh: openSymbol } = useDashboard();
  const router = useRouter();
  const watchlist = useWatchlist();
  const { refresh: refreshList, setMember } = watchlist;
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [addNote, setAddNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    refreshList(STALE_AFTER_MS);
  }, [refreshList]);

  const open = useCallback(
    (symbol: string) => {
      openSymbol({ symbol });
      router.push("/");
    },
    [openSymbol, router],
  );

  const remove = useCallback(
    async (item: WatchlistItem) => {
      setRemoveError(null);
      const result = await setMember(item.symbol, false);
      if (!result.ok) setRemoveError(result.message);
    },
    [setMember],
  );

  const add = useCallback(
    async (symbol: string, name?: string) => {
      setAddNote(null);
      const result = await setMember(symbol, true);
      const code = symbol.replace(/\.TWO?$/, "");
      setAddNote(
        result.ok
          ? { ok: true, text: `已加入 ${name ? `${name}（${code}）` : symbol}` }
          : { ok: false, text: result.message },
      );
    },
    [setMember],
  );

  const items = useMemo(() => watchlist.watchlist?.items ?? [], [watchlist.watchlist]);
  const { phase, count, limit } = watchlist;
  const full = phase === "ready" && count >= limit;
  const memberSymbols = useMemo(
    () => new Set(items.map((item) => item.symbol.toUpperCase())),
    [items],
  );

  let body;
  if (phase === "unavailable") {
    body = (
      <EmptyState
        icon={<InfoIcon size={22} />}
        title="自選股需要 Google 帳號登入"
        action={
          <Link className="button" href="/">
            回到今日訊號
          </Link>
        }
      >
        {watchlist.message}
      </EmptyState>
    );
  } else if (!watchlist.watchlist) {
    body =
      phase === "error" ? (
        <EmptyState
          icon={<AlertIcon size={22} />}
          title="暫時無法載入自選股"
          action={
            <button type="button" className="button" onClick={watchlist.retry}>
              重試
            </button>
          }
        >
          {watchlist.message}
        </EmptyState>
      ) : (
        <ListSkeleton />
      );
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon={<StarIcon size={22} />}
        title="還沒有自選股"
        action={
          <Link className="button" href="/market">
            從市場排行找股票
          </Link>
        }
      >
        在上方搜尋股票代號或名稱，選到就會直接加入，最多 {limit} 檔。也可以在任一檔股票頁按下星號。
        {WATCHLIST_COVERAGE_NOTE}
      </EmptyState>
    );
  } else {
    body = (
      <>
        {phase === "error" && (
          <p className="inline-error" role="alert">
            {watchlist.message}（顯示的是上次載入的內容）
          </p>
        )}
        {removeError && (
          <p className="inline-error" role="alert">
            {removeError}
          </p>
        )}
        <Card className="watch-board" aria-label="自選股清單">
          <table className="watch-table">
            <caption className="sr-only">自選股清單，共 {items.length} 檔</caption>
            <thead>
              <tr>
                <th scope="col" className="col-stock">
                  股票
                </th>
                <th scope="col" className="col-close">
                  收盤
                </th>
                <th scope="col" className="col-change">
                  當日漲跌
                </th>
                <th scope="col" className="col-flow1">
                  外資 1 日（張）
                </th>
                <th scope="col" className="col-flow5">
                  三大法人 5 日（張）
                </th>
                <th scope="col" className="col-status">
                  分析狀態
                </th>
                <th scope="col" className="col-remove">
                  <span className="sr-only">移除</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <Row
                  key={item.symbol}
                  item={item}
                  removing={watchlist.pending.has(item.symbol.toUpperCase())}
                  onOpen={open}
                  onRemove={remove}
                />
              ))}
            </tbody>
          </table>
        </Card>
        <footer className="market-foot" aria-label="資料說明">
          <p className="num">{dataFootnote(items)}</p>
          <p>
            買賣超單位為張（1 張 = 1,000 股）；外資 1 日是各檔最新已公布的一個交易日，三大法人 5 日是最近 5 個已公布交易日的合計。
            {WATCHLIST_COVERAGE_NOTE}
          </p>
        </footer>
      </>
    );
  }

  return (
    <>
      <div className="view-heading watch-heading">
        <div>
          <h1>自選股</h1>
          <p>追蹤你關心的股票；點選任一檔，就會在今日訊號頁開啟。</p>
        </div>
        {watchlist.watchlist && phase !== "unavailable" && (
          <span
            className="chip plain num watch-count"
            aria-label={`已加入 ${count} 檔，上限 ${limit} 檔`}
          >
            {count} / {limit}
          </span>
        )}
      </div>
      {phase !== "unavailable" && (
        <div className="watch-add">
          <SymbolCombobox
            value=""
            recents={[]}
            label="加入自選股"
            placeholder={full ? `已滿 ${limit} 檔，請先移除一檔` : "搜尋代號或名稱，選取後直接加入"}
            resetOnSelect
            disabled={full || phase === "loading"}
            markedSymbols={memberSymbols}
            onSelect={add}
          />
          <p
            className={addNote?.ok === false ? "inline-error" : "watch-add-note"}
            role={addNote?.ok === false ? "alert" : "status"}
          >
            {addNote?.text}
          </p>
        </div>
      )}
      {full && (
        <div className="callout info" role="status">
          <InfoIcon size={16} />
          <p>自選股已滿 {limit} 檔；想加入新的股票，請先移除一檔。</p>
        </div>
      )}
      {body}
    </>
  );
}
