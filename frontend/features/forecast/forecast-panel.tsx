"use client";

import { FormEvent, useEffect, useState } from "react";
import { CardHeading } from "@/components/card-heading";
import { ScenarioChart } from "@/components/charts/scenario-chart";
import { EmptyPanel } from "@/components/empty-panel";
import { Metric, MetricGrid } from "@/components/metric-grid";
import { ResearchTable } from "@/components/research-table";
import { JobStatusChip } from "@/components/status-chip";
import { createForecast, getForecastStatus, syncSymbol } from "@/lib/api/endpoints";
import type { ForecastReport } from "@/lib/api/types";
import { formatPrice, formatRatio } from "@/lib/format";

export function ForecastPanel({ symbol, asOf }: { symbol: string; asOf: string }) {
  const [model, setModel] = useState("bootstrap");
  const [horizon, setHorizon] = useState(5);
  const [lookback, setLookback] = useState(120);
  const [kronos, setKronos] = useState(false);
  const [report, setReport] = useState<ForecastReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getForecastStatus()
      .then((status) => {
        if (active) setKronos(status.kronos);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  async function run(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      setReport(
        await createForecast({ symbol: symbol.trim(), as_of: asOf, horizon, lookback, model }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "無法產生預測");
    } finally {
      setLoading(false);
    }
  }

  async function syncHistory() {
    setSyncing(true);
    setError(null);
    setSyncNote(null);
    setReport(null);
    const start = new Date(`${asOf}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - Math.ceil(lookback * 1.8) - 90);
    try {
      await syncSymbol({
        symbol: symbol.trim(),
        start: start.toISOString().slice(0, 10),
        end: asOf,
      });
      setSyncNote("官方日 K 同步完成，可重新產生預測。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "歷史資料同步失敗");
    } finally {
      setSyncing(false);
    }
  }

  const lastPoint = report?.points[report.points.length - 1];

  return (
    <section className="backtest-layout forecast-layout">
      <form className="backtest-form" onSubmit={run}>
        <div>
          <p className="eyebrow">FORECAST LAB</p>
          <h2>看見可能的路徑。</h2>
          <p>以截止日前的日 K 產生未來情境，再用歷史資料檢查統計基準的表現。</p>
        </div>
        <label>
          <span>預測模型</span>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="bootstrap">統計基準 · 報酬率重抽樣</option>
            <option value="kronos" disabled={!kronos}>
              Kronos-small · {kronos ? "實驗模型" : "尚未就緒"}
            </option>
          </select>
        </label>
        <label>
          <span>預測期間</span>
          <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
            {[1, 5, 20].map((n) => (
              <option key={n} value={n}>
                {n} 個交易日
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>歷史觀察窗口</span>
          <select value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
            {[60, 120, 250, 512].map((n) => (
              <option key={n} value={n}>
                {n} 根日 K
              </option>
            ))}
          </select>
        </label>
        <div className="cost-note">
          {symbol.toUpperCase()} · 截止 {asOf}
          <br />
          Kronos {kronos ? "服務已就緒" : "服務尚未連接"}；統計基準可獨立使用。
        </div>
        <button className="primary-button full" disabled={loading || syncing}>
          {loading ? "正在生成情境…" : "產生預測"}
        </button>
        <button
          className="history-sync-button"
          type="button"
          disabled={loading || syncing}
          onClick={syncHistory}
        >
          {syncing ? "正在同步官方日 K…" : "同步官方日 K"}
        </button>
        {syncNote && (
          <p className="research-note" role="status">
            {syncNote}
          </p>
        )}
        <p className="research-note">
          預測尚未用於策略回測的進出場判斷。歷史驗證與策略報酬是不同的衡量方式。
        </p>
      </form>
      <div className="backtest-result">
        <CardHeading
          title="價格情境與驗證"
          aside={
            <JobStatusChip>
              {report ? (report.model === "kronos" ? "Kronos-small" : "統計基準") : "研究模式"}
            </JobStatusChip>
          }
        />
        {error && (
          <p className="forecast-error" role="alert">
            {error}
          </p>
        )}
        {report && lastPoint ? (
          <>
            <MetricGrid>
              <Metric label="最後收盤" value={formatPrice(report.last_close)} />
              <Metric
                label={`${report.horizon} 日中位情境變動`}
                value={formatRatio(report.expected_return)}
                valueClassName={report.expected_return >= 0 ? "positive" : "negative"}
              />
              <Metric label="期末中位價格" value={formatPrice(lastPoint.median)} />
              <Metric label="資料截止日" value={report.data_through} valueClassName="date-metric" />
            </MetricGrid>
            <ScenarioChart report={report} />
            <div className="chart-legend">
              <span className="legend-history">歷史價格</span>
              <span className="legend-strategy">預測中位數</span>
              <span className="legend-band">10–90% 情境區間</span>
            </div>
            <ResearchTable
              label="預測情境價格表"
              headers={["交易日", "偏低情境", "中位情境", "偏高情境"]}
            >
              {report.points.map((p) => (
                <tr key={p.trading_date}>
                  <td>{p.trading_date}</td>
                  <td>{formatPrice(p.lower)}</td>
                  <td>{formatPrice(p.median)}</td>
                  <td>{formatPrice(p.upper)}</td>
                </tr>
              ))}
            </ResearchTable>
            <div className="validation-card">
              <p className="eyebrow">WALK-FORWARD · 統計基準模型</p>
              <h3>逐期驗證，不重看未來</h3>
              <p>
                使用最多 24 個不重疊的歷史窗口。每次僅使用當時已可取得的資料，與「價格維持不變」比較。
              </p>
              <MetricGrid>
                <Metric label="驗證窗口" value={report.evaluation.windows} />
                <Metric label="方向命中率" value={formatRatio(report.evaluation.direction_accuracy)} />
                <Metric
                  label="平均絕對誤差 / 當期價格"
                  value={formatRatio(report.evaluation.mae_pct)}
                />
                <Metric label="價格不變基準誤差" value={formatRatio(report.evaluation.naive_mae_pct)} />
              </MetricGrid>
              <p>
                80% 情境區間的歷史涵蓋率：{formatRatio(report.evaluation.interval_coverage)}。
                {report.evaluation.windows < 10 && "樣本偏少，請補足歷史資料後再評估。"}
              </p>
              {report.model === "kronos" && (
                <p className="forecast-error">
                  這組驗證數據屬於統計基準，Kronos 的台股樣本外績效尚未驗證。
                </p>
              )}
            </div>
            <div className="research-note">
              {report.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          </>
        ) : (
          !loading && (
            <EmptyPanel icon="⌁" title="先有預測，再看證據">
              選擇模型與期間，查看未來價格情境及統計基準的歷史驗證。
            </EmptyPanel>
          )
        )}
        {loading && (
          <EmptyPanel icon="⌁" title="正在計算預測路徑" live>
            Kronos 首次運算可能需要較長時間。
          </EmptyPanel>
        )}
      </div>
    </section>
  );
}
