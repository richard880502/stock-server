"use client";

import { FormEvent, useEffect, useState } from "react";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { AlertIcon, ChevronDownIcon, FlaskIcon, InfoIcon } from "@/components/icons";
import { ResearchTable } from "@/components/research-table";
import { Stat, StatGrid } from "@/components/stat";
import { ScenarioChart } from "@/components/charts/scenario-chart";
import { RateLimitNotice } from "@/components/rate-limit-notice";
import { rateLimitInfo } from "@/lib/api/rate-limit";
import { createForecast, getForecastStatus, syncSymbol } from "@/lib/api/endpoints";
import type { ForecastReport } from "@/lib/api/types";
import { useScrollIntoViewOnStart } from "@/lib/use-scroll-into-view";
import { formatPrice, formatRatio, formatSignedPct } from "@/lib/format";

/** Plain-language read of the walk-forward numbers (never "beats the market"). */
function validationVerdict(evaluation: ForecastReport["evaluation"]) {
  const { mae_pct: mae, naive_mae_pct: naive, windows } = evaluation;
  if (windows < 10) return { tone: "warn", text: "驗證窗口偏少，下面的數字僅供參考；請補足歷史資料後再評估。" };
  if (mae === null || naive === null) return { tone: "flat", text: "驗證資料不足，無法與「價格不變」基準比較。" };
  if (mae < naive * 0.98)
    return { tone: "flat", text: `平均誤差 ${formatRatio(mae)}，略低於「價格不變」的 ${formatRatio(naive)}。這只代表過去窗口的表現，不保證未來。` };
  return { tone: "warn", text: `平均誤差 ${formatRatio(mae)}，並未明顯優於「價格不變」的 ${formatRatio(naive)}；請把情境區間當作波動範圍，而非方向判斷。` };
}

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

  async function run(event?: FormEvent) {
    event?.preventDefault();
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

  useScrollIntoViewOnStart(loading, "forecast-result");
  const lastPoint = report?.points[report.points.length - 1];
  const verdict = report ? validationVerdict(report.evaluation) : null;

  return (
    <div className="workbench">
      <Card as="div" title="預測設定" sub={`${symbol.toUpperCase()} · 截止 ${asOf}`}>
        <form className="form-stack" onSubmit={run}>
          <label className="field">
            <span className="field-label">預測模型</span>
            <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="bootstrap">統計基準 · 報酬率重抽樣</option>
              <option value="kronos" disabled={!kronos}>
                Kronos-small · {kronos ? "實驗模型" : "尚未就緒"}
              </option>
            </select>
            <span className="field-hint">
              Kronos {kronos ? "服務已就緒" : "服務尚未連接"}；統計基準可獨立使用。
            </span>
          </label>
          <div className="field-row">
            <label className="field">
              <span className="field-label">預測期間</span>
              <select className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                {[1, 5, 20].map((n) => (
                  <option key={n} value={n}>
                    {n} 個交易日
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">歷史觀察窗口</span>
              <select className="input" value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
                {[60, 120, 250, 512].map((n) => (
                  <option key={n} value={n}>
                    {n} 根日 K
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button className="button primary block" disabled={loading || syncing}>
            {loading ? (
              <>
                <span className="spinner small" aria-hidden="true" />
                正在生成情境
              </>
            ) : (
              "產生預測"
            )}
          </button>
          <button className="button block" type="button" disabled={loading || syncing} onClick={syncHistory}>
            {syncing ? (
              <>
                <span className="spinner small" aria-hidden="true" />
                正在同步官方日 K
              </>
            ) : (
              "同步官方日 K"
            )}
          </button>
          {syncNote && (
            <p className="callout info" role="status">
              <InfoIcon size={16} />
              <span>{syncNote}</span>
            </p>
          )}
          <p className="note">
            預測尚未用於策略回測的進出場判斷。歷史驗證與策略報酬是不同的衡量方式。
          </p>
        </form>
      </Card>

      <div id="forecast-result" className="scroll-target">
        <Card
          as="div"
          title="價格情境與驗證"
          sub={report ? `資料截止 ${report.data_through}` : undefined}
          aside={
            <span className="chip plain">
              {report ? (report.model === "kronos" ? "Kronos-small" : "統計基準") : "研究模式"}
            </span>
          }
          className="result-card"
        >
          {error && rateLimitInfo(error) && (
            <RateLimitNotice message={error} retryAfterSeconds={rateLimitInfo(error)?.retryAfterSeconds ?? null} />
          )}
          {error && !rateLimitInfo(error) && (
            <div className="inline-error" role="alert">
              <p>
                <strong>無法完成這項預測。</strong> {error}
              </p>
              <p className="field-hint">
                若是歷史資料不足，可先按左側「同步官方日 K」，完成後再產生預測。
              </p>
            </div>
          )}
          {loading ? (
            <div className="result-stack" aria-busy="true" role="status" aria-label="正在計算預測路徑">
              <div className="progress-line" />
              <span className="skeleton block" style={{ height: 88 }} />
              <span className="skeleton block" style={{ height: 300 }} />
              <p className="note">Kronos 首次運算可能需要較長時間。</p>
            </div>
          ) : report && lastPoint ? (
            <div className="result-stack">
              <div className="result-hero">
                <Stat
                  label={`${report.horizon} 日後中位情境`}
                  valueClassName="result-hero-value"
                  value={formatPrice(lastPoint.median)}
                  note={
                    <>
                      相對最後收盤 {formatPrice(report.last_close)}{" "}
                      <Direction value={report.expected_return}>{formatSignedPct(report.expected_return)}</Direction>
                    </>
                  }
                />
                <Stat
                  label="10–90% 情境區間"
                  value={`${formatPrice(lastPoint.lower)} – ${formatPrice(lastPoint.upper)}`}
                  note="約八成的模擬路徑落在此範圍"
                />
              </div>

              <ScenarioChart report={report} />

              <details className="disclosure">
                <summary>
                <span>每日情境價格表（{report.points.length} 日）</span>
                <ChevronDownIcon size={16} />
              </summary>
                <div className="disclosure-body">
                  <ResearchTable label="預測情境價格表" headers={["交易日", "偏低情境", "中位情境", "偏高情境"]}>
                    {report.points.map((p) => (
                      <tr key={p.trading_date}>
                        <td>{p.trading_date}</td>
                        <td>{formatPrice(p.lower)}</td>
                        <td>{formatPrice(p.median)}</td>
                        <td>{formatPrice(p.upper)}</td>
                      </tr>
                    ))}
                  </ResearchTable>
                </div>
              </details>

              <section className="validation" aria-labelledby="validation-title">
                <div className="card-header">
                  <div>
                    <h3 className="card-title" id="validation-title">
                      逐期驗證：這個方法過去準不準
                    </h3>
                    <p className="card-sub">
                      最多 24 個不重疊的歷史窗口，每次只使用當時已知的資料，與「價格維持不變」比較。
                    </p>
                  </div>
                </div>
                <StatGrid>
                  <Stat label="驗證窗口" value={report.evaluation.windows} />
                  <Stat label="方向命中率" value={formatRatio(report.evaluation.direction_accuracy)} note="猜對漲跌方向的比例" />
                  <Stat label="模型平均誤差" value={formatRatio(report.evaluation.mae_pct)} note="平均絕對誤差 / 當期價格" />
                  <Stat label="價格不變誤差" value={formatRatio(report.evaluation.naive_mae_pct)} note="比較基準" />
                  <Stat label="80% 區間涵蓋率" value={formatRatio(report.evaluation.interval_coverage)} note="實際落在區間內的比例" />
                </StatGrid>
                {verdict && (
                  <p className={`callout${verdict.tone === "flat" ? " info" : ""}`}>
                    <InfoIcon size={16} />
                    <span>{verdict.text}</span>
                  </p>
                )}
                {report.model === "kronos" && (
                  <p className="callout">
                    <AlertIcon size={16} />
                    <span>這組驗證數據屬於統計基準，Kronos 的台股樣本外績效尚未驗證。</span>
                  </p>
                )}
              </section>

              {report.warnings.length > 0 && (
                <div className="note-list">
                  {report.warnings.map((w) => (
                    <p className="note" key={w}>
                      {w}
                    </p>
                  ))}
                </div>
              )}
            </div>
          ) : (
            !error && (
              <EmptyState icon={<FlaskIcon size={22} />} title="先有預測，再看證據">
                選擇模型與期間，查看未來價格情境，以及統計基準在歷史上的表現。預測是情境，不是答案。
              </EmptyState>
            )
          )}
        </Card>
      </div>
    </div>
  );
}
