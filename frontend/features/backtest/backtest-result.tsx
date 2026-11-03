"use client";

import { EquityChart } from "@/components/charts/equity-chart";
import { CardHeading } from "@/components/card-heading";
import { EmptyPanel } from "@/components/empty-panel";
import { Metric, MetricGrid } from "@/components/metric-grid";
import { ResearchTable } from "@/components/research-table";
import { JobStatusChip } from "@/components/status-chip";
import { useDashboard } from "@/lib/dashboard-context";
import { formatNumber, formatPct } from "@/lib/format";

export function BacktestResult() {
  const { backtest } = useDashboard();
  const job = backtest.job;
  const result = job?.result;

  return (
    <div className="backtest-result">
      <CardHeading
        title="回測結果"
        aside={job && <JobStatusChip status={job.status}>{job.status}</JobStatusChip>}
      />
      {result ? (
        <>
          <MetricGrid>
            <Metric
              label="總報酬"
              value={formatPct(result.total_return)}
              valueClassName={result.total_return >= 0 ? "positive" : "negative"}
            />
            <Metric label="年化報酬" value={formatPct(result.annualized_return)} />
            <Metric
              label="最大回撤"
              value={formatPct(result.max_drawdown)}
              valueClassName="negative"
            />
            <Metric
              label="Sharpe"
              value={result.sharpe === null ? "—" : formatNumber(result.sharpe, 2)}
            />
          </MetricGrid>
          {result.warnings?.map((warning) => (
            <p className="forecast-error" key={warning}>
              {warning}
            </p>
          ))}
          <div className="comparison-summary">
            <span>
              同標的買入持有{" "}
              <strong>
                {result.buy_hold_return == null ? "—" : formatPct(result.buy_hold_return)}
              </strong>
            </span>
            <span>
              策略超額報酬{" "}
              <strong>
                {result.excess_return == null ? "—" : formatPct(result.excess_return)}
              </strong>
            </span>
          </div>
          <EquityChart strategy={result.equity_curve} buyHold={result.buy_hold_curve ?? []} />
          <div className="backtest-foot">
            <span>{result.observations} 個交易日</span>
            <span>{result.trades} 次換倉</span>
            <span>年化波動 {formatPct(result.annualized_volatility)}</span>
          </div>
          <p className="research-note">
            買入持有採相同起迄開盤價與買入成本；兩者期末皆按市值計價。上方「比較基準」用於相對強弱因子，此圖對照的是同標的買入持有。
          </p>
          {(result.transactions?.length ?? 0) > 0 && (
            <details className="trade-details">
              <summary>交易明細（{result.transactions!.length} 筆）</summary>
              <ResearchTable
                label="回測交易明細"
                headers={["日期", "方向", "原始開盤價", "成本", "交易後淨值"]}
              >
                {result.transactions!.map((t) => (
                  <tr key={t.trading_date}>
                    <td>{t.trading_date}</td>
                    <td>{t.side === "buy" ? "買入" : "賣出"}</td>
                    <td>{formatNumber(t.price, 2)}</td>
                    <td>{formatNumber(t.cost, 2)}</td>
                    <td>{formatNumber(t.equity, 0)}</td>
                  </tr>
                ))}
              </ResearchTable>
            </details>
          )}
        </>
      ) : (
        <EmptyPanel icon="↗" title="等待策略驗證">
          設定期間與門檻後啟動回測，結果會由獨立 worker 計算。
        </EmptyPanel>
      )}
    </div>
  );
}
