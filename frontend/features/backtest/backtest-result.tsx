"use client";

import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { AlertIcon, ChartIcon, InfoIcon } from "@/components/icons";
import { ResearchTable } from "@/components/research-table";
import { Stat, StatGrid } from "@/components/stat";
import { JobStatusChip } from "@/components/status-chip";
import { EquityChart } from "@/components/charts/equity-chart";
import { useDashboard } from "@/lib/dashboard-context";
import { formatNumber, formatPct, formatSignedPct } from "@/lib/format";

function ResultSkeleton({ status }: { status: string }) {
  return (
    <div className="result-stack" aria-busy="true">
      <div className="progress-line" role="progressbar" aria-label={`回測${status}`} />
      <span className="skeleton block" style={{ height: 88 }} />
      <span className="skeleton block" style={{ height: 280 }} />
    </div>
  );
}

export function BacktestResult() {
  const { backtest, query } = useDashboard();
  const { job, loading, run } = backtest;
  const result = job?.result;

  return (
    <Card
      as="div"
      title="回測結果"
      sub={job ? `${query.symbol} · 任務 ${job.id.slice(0, 8)}` : undefined}
      aside={job && <JobStatusChip status={job.status} />}
      className="result-card"
    >
      {job?.status === "failed" ? (
        <EmptyState
          icon={<AlertIcon size={22} />}
          title="這次回測沒有完成"
          action={
            <button type="button" className="button" onClick={() => void run()} disabled={loading}>
              再試一次
            </button>
          }
        >
          {job.error ?? "worker 回報失敗，請稍後再試，或調整期間與門檻後重新啟動。"}
        </EmptyState>
      ) : !job ? (
        loading ? (
          <ResultSkeleton status="建立中" />
        ) : (
          <EmptyState icon={<ChartIcon size={22} />} title="等待策略驗證">
            設定期間與門檻後啟動回測，結果由獨立 worker 計算，完成後會與「同標的買入持有」並排比較。
          </EmptyState>
        )
      ) : !result ? (
        <ResultSkeleton status={job.status === "queued" ? "排隊中" : "計算中"} />
      ) : (
        <div className="result-stack">
          <div className="result-hero">
            <Stat
              label="策略總報酬"
              valueClassName="result-hero-value"
              value={
                <Direction value={result.total_return}>{formatSignedPct(result.total_return)}</Direction>
              }
              note={`${result.observations} 個交易日 · ${result.trades} 次換倉`}
            />
            <Stat
              label="同標的買入持有"
              value={
                result.buy_hold_return == null ? (
                  "—"
                ) : (
                  <Direction value={result.buy_hold_return}>
                    {formatSignedPct(result.buy_hold_return)}
                  </Direction>
                )
              }
            />
            <Stat
              label="策略超額報酬"
              value={
                result.excess_return == null ? (
                  "—"
                ) : (
                  <Direction value={result.excess_return}>{formatSignedPct(result.excess_return)}</Direction>
                )
              }
              note="策略 − 買入持有"
            />
          </div>

          {result.warnings?.map((warning) => (
            <p className="callout" key={warning}>
              <InfoIcon size={16} />
              <span>{warning}</span>
            </p>
          ))}

          <EquityChart strategy={result.equity_curve} buyHold={result.buy_hold_curve ?? []} />

          <StatGrid>
            <Stat label="年化報酬" value={<Direction value={result.annualized_return}>{formatSignedPct(result.annualized_return)}</Direction>} />
            <Stat label="年化波動" value={formatPct(result.annualized_volatility)} />
            <Stat
              label="最大回撤"
              value={<Direction value={result.max_drawdown === 0 ? 0 : -Math.abs(result.max_drawdown)}>{formatSignedPct(-Math.abs(result.max_drawdown))}</Direction>}
              note="期間內由高點回落的最大幅度"
            />
            <Stat label="Sharpe" value={result.sharpe === null ? "—" : formatNumber(result.sharpe, 2)} />
          </StatGrid>

          <p className="note">
            買入持有採相同起迄開盤價與買入成本；兩者期末皆按市值計價。上方「比較基準」用於相對強弱因子，圖中對照的是同標的買入持有。歷史回測不代表未來表現。
          </p>

          {(result.transactions?.length ?? 0) > 0 && (
            <details className="disclosure">
              <summary>交易明細（{result.transactions!.length} 筆）</summary>
              <div className="disclosure-body">
                <ResearchTable
                  label="回測交易明細"
                  headers={["日期", "方向", "原始開盤價", "成本", "交易後淨值"]}
                >
                  {result.transactions!.map((t, i) => (
                    <tr key={`${t.trading_date}-${i}`}>
                      <td>{t.trading_date}</td>
                      <td>{t.side === "buy" ? "買入" : "賣出"}</td>
                      <td>{formatNumber(t.price, 2)}</td>
                      <td>{formatNumber(t.cost, 2)}</td>
                      <td>{formatNumber(t.equity, 0)}</td>
                    </tr>
                  ))}
                </ResearchTable>
              </div>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}
