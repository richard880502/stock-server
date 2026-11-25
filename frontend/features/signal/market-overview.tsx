"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Card } from "@/components/card";
import { getMarketRankings } from "@/lib/api/endpoints";
import { useDashboard } from "@/lib/dashboard-context";
import { formatSignedLots } from "@/lib/format";
import { useCommittedAsOf } from "@/features/market/use-committed-as-of";
import { useMarketResource } from "@/features/market/use-market-resource";
import { AwaitingReportNotice } from "@/features/market/market-notices";
import { MarketPanel } from "./market-panel";

export function MarketOverview() {
  const { query, signalData, refresh } = useDashboard();
  const router = useRouter();
  const asOf = useCommittedAsOf(query.asOf);
  const ranking = useMarketResource(`home|total_net|ALL|1|${asOf}`, (signal) =>
    getMarketRankings({ metric: "total_net", market: "ALL", window: 1, order: "desc", limit: 5, asOf }, { signal }),
  );
  const data = ranking.stale ? null : ranking.data;
  const environment = signalData.signal?.symbol === query.symbol && !signalData.loading
    ? signalData.marketEnvironment : null;
  const pick = (symbol: string, destination: "/" | "/backtest") => {
    refresh({ symbol, asOf });
    if (destination === "/") document.getElementById("stock-signal")?.scrollIntoView({ behavior: "smooth" });
    else router.push(destination);
  };
  return (
    <section className="home-market" aria-labelledby="home-market-title">
      <div className="view-heading">
        <h1 id="home-market-title">市場與今日訊號</h1>
        <p>先掌握市場背景，再從排行選股查看訊號或進行策略回測。</p>
      </div>
      {environment ? (
        <MarketPanel market={query.market} environment={environment} asOf={asOf} />
      ) : (
        <Card title="市場環境" sub="依目前選取的股票顯示上市或上櫃市場">
          <p className="note">{signalData.loading ? "市場環境載入中…" : "目前沒有市場環境資料。"}</p>
        </Card>
      )}
      <Card title="市場候選股" sub="上市與上櫃 · 三大法人當日買賣超由高到低；這是排行，並非買進建議。" aside={<Link href="/market" className="button small">深入市場 →</Link>}>
        {data?.awaiting_report_date && <AwaitingReportNotice report="三大法人" awaiting={data.awaiting_report_date} through={data.data_through} />}
        {data?.backfill_pending && <p className="note" role="status">資料回補中，排行可能不完整，頁面會自動更新。</p>}
        {ranking.error ? <p className="note" role="alert">{ranking.error} <button className="button small" onClick={ranking.retry}>重試</button></p>
          : !data ? <p className="note" role="status">載入市場排行中…</p>
          : data.entries.length === 0 ? <p className="note">{data.backfill_pending ? "資料回補中，暫無排行。" : "此日期沒有可排行的資料。"}</p>
          : <>
            <p className="note">資料截止 {data.data_through ?? "未知"} · 數值單位：張</p>
            <ul className="home-candidates">
              {data.entries.map((entry) => <li key={entry.symbol}>
                <span><strong>{entry.symbol.replace(/\.TWO?$/, "")} {entry.name}</strong><small>{entry.market === "TWO" ? "上櫃" : "上市"} · {formatSignedLots(entry.value)} 張</small></span>
                <span className="home-candidate-actions"><button className="button small" onClick={() => pick(entry.symbol, "/")}>查看訊號</button><button className="button small" onClick={() => pick(entry.symbol, "/backtest")}>策略回測</button></span>
              </li>)}
            </ul>
          </>}
      </Card>
    </section>
  );
}
