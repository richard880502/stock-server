"use client";

import { useEffect, useState } from "react";
import { getMarketDataStatus } from "@/lib/api/endpoints";
import type { MarketDataStatus } from "@/lib/api/types";
import { formatLots } from "@/lib/format";
import { SCOPE_NAME } from "./metrics";

const percent = (ratio: number) => `${Math.round(ratio * 100)}%`;

/**
 * Data freshness per market plus the plain-language caveats. `refreshKey`
 * changes whenever the flows poll brings new data, so the figures follow the backfill.
 */
export function MarketDataFooter({ refreshKey }: { refreshKey: unknown }) {
  const [rows, setRows] = useState<MarketDataStatus[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getMarketDataStatus({ signal: controller.signal })
      .then(setRows)
      .catch(() => {
        // The footer is optional: keep whatever was shown last.
      });
    return () => controller.abort();
  }, [refreshKey]);

  return (
    <footer className="market-foot" aria-label="資料說明">
      {rows && rows.length > 0 && (
        <dl className="data-status">
          {rows.map((row) => (
            <div key={row.market} className="data-status-row">
              <dt>{SCOPE_NAME[row.market]}</dt>
              <dd className="num">
                {row.first_date && row.last_date ? `${row.first_date} 至 ${row.last_date}` : "尚無資料"}
                {" · "}
                {row.trading_days} 個交易日 · 最新 {formatLots(row.stocks_on_last_day)} 檔 · 三大法人涵蓋{" "}
                {percent(row.institutional_coverage)} · 融資融券涵蓋 {percent(row.credit_coverage)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <p>
        資料來自證券交易所與櫃買中心的官方收盤後報告，不是即時行情；三大法人與融資融券通常在收盤後數小時才公布，尚未公布的日子會明確標示，不會當作 0。
      </p>
      <p>
        排行與趨勢是研究參考，不構成投資建議。三大法人是公開的法人合計，並非個別券商分點；本頁沒有分點資料，也不提供相關排行。
      </p>
    </footer>
  );
}
