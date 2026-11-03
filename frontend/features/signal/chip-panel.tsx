import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { ChevronDownIcon } from "@/components/icons";
import { Stat } from "@/components/stat";
import type { ChipFlow } from "@/lib/api/types";
import { formatNumber, formatPct, formatSigned } from "@/lib/format";

const lots = (shares: number) => shares / 1000;

export function ChipPanel({ chipFlow }: { chipFlow: ChipFlow | null }) {
  if (!chipFlow) {
    return (
      <Card title="法人與分點籌碼" sub="尚未匯入足夠的個股法人資料" aria-label="法人與分點籌碼">
        <p className="note">
          三大法人買賣超需要足夠的歷史資料才能計算；取得資料後，這裡會顯示外資、投信與自營商的近 5 日動向。
        </p>
      </Card>
    );
  }

  const { metrics } = chipFlow;
  return (
    <Card
      title="法人與分點籌碼"
      sub={`${chipFlow.institutional_observations} 個法人交易日 · 截至 ${chipFlow.as_of}`}
      aside={
        <span className="chip plain num" title="籌碼分數">
          籌碼分數 {formatNumber(chipFlow.score, 0)}
        </span>
      }
      aria-label="法人與分點籌碼"
    >
      <div className="stat-grid chip-stats">
        <Stat
          label="外資 5 日買賣超"
          value={
            <Direction value={metrics.foreign_net_5d}>
              {formatSigned(lots(metrics.foreign_net_5d), 0)} 張
            </Direction>
          }
          note={`連續 ${metrics.foreign_streak} 日`}
        />
        <Stat
          label="投信 5 日買賣超"
          value={
            <Direction value={metrics.investment_trust_net_5d}>
              {formatSigned(lots(metrics.investment_trust_net_5d), 0)} 張
            </Direction>
          }
          note={`連續 ${metrics.investment_trust_streak} 日`}
        />
        <Stat
          label="自營商 5 日買賣超"
          value={
            <Direction value={metrics.dealer_net_5d}>
              {formatSigned(lots(metrics.dealer_net_5d), 0)} 張
            </Direction>
          }
        />
        <Stat
          label="外資持股比率"
          value={
            metrics.foreign_holding_ratio != null
              ? `${formatNumber(metrics.foreign_holding_ratio, 2)}%`
              : "—"
          }
          note={
            metrics.foreign_holding_change_5d != null
              ? `5 日變化 ${formatSigned(metrics.foreign_holding_change_5d, 2)} pp`
              : "待資料"
          }
        />
      </div>

      <details className="disclosure">
        <summary>
          <span>
            分點資料：
            {chipFlow.branch_data_available ? "已載入授權資料" : "尚未授權／匯入"}
          </span>
          <ChevronDownIcon size={16} />
        </summary>
        <div className="disclosure-body">
          <p className="note">
            {chipFlow.branch_data_available
              ? `共 ${chipFlow.branch_observations} 筆，可計算集中度、成本、短線活動與分點勝率。`
              : "三大法人訊號照常運作；取得 TWSE／TPEx 或供應商授權檔後即可啟用分點分析。"}
          </p>
          {chipFlow.branch_data_available && (
            <div className="stat-grid" style={{ marginTop: "var(--space-3)" }}>
              <Stat
                label="買方集中度"
                value={
                  metrics.branch_buy_concentration_5d != null
                    ? formatPct(metrics.branch_buy_concentration_5d)
                    : "—"
                }
              />
              <Stat
                label="平均成本"
                value={
                  metrics.branch_estimated_cost != null
                    ? formatNumber(metrics.branch_estimated_cost, 2)
                    : "—"
                }
              />
              <Stat
                label="短線比率"
                value={
                  metrics.branch_day_trade_ratio != null
                    ? formatPct(metrics.branch_day_trade_ratio)
                    : "—"
                }
              />
            </div>
          )}
        </div>
      </details>
    </Card>
  );
}
