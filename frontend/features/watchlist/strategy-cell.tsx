"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { WatchlistItem } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import { backtestHref } from "@/lib/stock-routes";
import { benchmarkForSymbol, DEFAULT_BENCHMARK } from "@/lib/symbols";
import { readJson } from "@/lib/safe-storage";
import { emptyEntry, type StrategyEntry } from "./strategy-entry";
import { StrategyDrawer } from "./strategy-drawer";

export function StrategyCell({ item, userId }: { item: WatchlistItem; userId: string }) {
  const storageKey = `qs.watch-research.v1:${userId}:${item.symbol}`;
  const [entry, setEntry] = useState(() => readJson<StrategyEntry>(storageKey, emptyEntry) ?? emptyEntry);
  const latest = useRef(entry);
  const [open, setOpen] = useState(false);
  const [storageError, setStorageError] = useState(false);
  const { refresh } = useDashboard();
  const router = useRouter();
  const update = (patch: Partial<StrategyEntry>) => {
    const next = { ...latest.current, ...patch };
    latest.current = next;
    setEntry(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  };
  const judgement = entry.report?.judgement;
  const changed = entry.previous && judgement && entry.previous.judgement.stance !== judgement.stance;
  const newData = entry.report && ((item.data_through ?? "") > (entry.dataThrough ?? "") ||
    (item.flows_through ?? "") > (entry.flowsThrough ?? ""));
  return (
    <td className="col-strategy" onClick={(event) => event.stopPropagation()}>
      <div className="watch-strategy-summary">
        <small>{entry.held ? "已持有" : "追蹤中"}{changed && " · 研究立場改變"}{newData && " · 有新資料"}</small>
        {judgement && <><strong>{judgement.headline}</strong>
          <span>{(entry.held ? entry.report?.risk_review?.invalidation_conditions[0] : judgement.scenarios[0]?.condition) ?? judgement.operation_guide}</span>
          <small>分析截至 {entry.report?.as_of}</small></>}
        <button className="button small" type="button" onClick={() => setOpen(true)} aria-haspopup="dialog">
          {entry.report ? "查看策略" : "開啟策略研究"}
        </button>
        {storageError && <small role="status">裝置儲存空間無法使用，這次更新僅保留於目前頁面。</small>}
      </div>
      {open && <StrategyDrawer item={item} entry={entry} update={update} onClose={() => setOpen(false)}
        onBacktest={(asOf) => {
          refresh({ symbol: item.symbol, benchmark: benchmarkForSymbol(item.symbol, DEFAULT_BENCHMARK), asOf });
          setOpen(false);
          router.push(backtestHref(item.symbol));
        }} />}
    </td>
  );
}
