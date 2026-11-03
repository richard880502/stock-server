import type { ChipFlow } from "@/lib/api/types";
import { formatNumber, formatPct } from "@/lib/format";

export function ChipPanel({ chipFlow }: { chipFlow: ChipFlow | null }) {
  const metrics = chipFlow?.metrics;
  return (
    <section className="chip-panel" aria-label="法人與分點籌碼">
      <div className="chip-summary">
        <div>
          <p className="eyebrow">STOCK CHIP FLOW</p>
          <h2>法人與分點籌碼</h2>
          <span>
            {chipFlow
              ? `${chipFlow.institutional_observations} 個法人交易日`
              : "尚未匯入足夠的個股法人資料"}
          </span>
        </div>
        <strong>{chipFlow ? formatNumber(chipFlow.score, 0) : "—"}</strong>
        <small>籌碼分數</small>
      </div>
      <div className="chip-metrics">
        <div>
          <span>外資 5 日</span>
          <strong>{metrics ? formatNumber(metrics.foreign_net_5d / 1000, 0) : "—"}</strong>
          <small>張 · 連續 {metrics?.foreign_streak ?? "—"} 日</small>
        </div>
        <div>
          <span>投信 5 日</span>
          <strong>
            {metrics ? formatNumber(metrics.investment_trust_net_5d / 1000, 0) : "—"}
          </strong>
          <small>張 · 連續 {metrics?.investment_trust_streak ?? "—"} 日</small>
        </div>
        <div>
          <span>自營商 5 日</span>
          <strong>{metrics ? formatNumber(metrics.dealer_net_5d / 1000, 0) : "—"}</strong>
          <small>張</small>
        </div>
        <div>
          <span>外資持股</span>
          <strong>
            {metrics?.foreign_holding_ratio != null
              ? `${formatNumber(metrics.foreign_holding_ratio, 2)}%`
              : "—"}
          </strong>
          <small>
            5 日變化{" "}
            {metrics?.foreign_holding_change_5d != null
              ? `${formatNumber(metrics.foreign_holding_change_5d, 2)} pp`
              : "待資料"}
          </small>
        </div>
      </div>
      <div className="branch-status">
        <div>
          <span className={chipFlow?.branch_data_available ? "connected" : ""}>
            {chipFlow?.branch_data_available ? "已載入授權分點資料" : "分點資料尚未授權／匯入"}
          </span>
          <p>
            {chipFlow?.branch_data_available
              ? `共 ${chipFlow.branch_observations} 筆，可計算集中度、成本、短線活動與分點勝率。`
              : "三大法人訊號照常運作；取得 TWSE／TPEx 或供應商授權檔後即可啟用分點分析。"}
          </p>
        </div>
        <div className="branch-kpis">
          <span>
            買方集中{" "}
            <b>
              {metrics?.branch_buy_concentration_5d != null
                ? formatPct(metrics.branch_buy_concentration_5d)
                : "—"}
            </b>
          </span>
          <span>
            平均成本{" "}
            <b>
              {metrics?.branch_estimated_cost != null
                ? formatNumber(metrics.branch_estimated_cost, 2)
                : "—"}
            </b>
          </span>
          <span>
            短線比率{" "}
            <b>
              {metrics?.branch_day_trade_ratio != null
                ? formatPct(metrics.branch_day_trade_ratio)
                : "—"}
            </b>
          </span>
        </div>
      </div>
    </section>
  );
}
