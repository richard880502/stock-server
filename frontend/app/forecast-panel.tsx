"use client";

import { FormEvent, useEffect, useState } from "react";

type Point = { trading_date: string; median: number; lower: number; upper: number };
type Report = {
  symbol: string; model: string; data_through: string; last_close: number;
  expected_return: number; lookback: number; horizon: number;
  history: { trading_date: string; close: number }[]; points: Point[];
  evaluation: { windows: number; mae_pct: number | null; naive_mae_pct: number | null;
    direction_accuracy: number | null; interval_coverage: number | null };
  warnings: string[];
};
const pct = (v: number | null) => v === null ? "—" : `${(v * 100).toFixed(1)}%`;
const price = (v: number) => v.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
type Fetcher = <T>(url: string, init?: RequestInit) => Promise<T>;

export function ForecastPanel({ symbol, asOf, apiUrl, fetcher }: {
  symbol: string; asOf: string; apiUrl: string; fetcher: Fetcher;
}) {
  const [model, setModel] = useState("bootstrap");
  const [horizon, setHorizon] = useState(5);
  const [lookback, setLookback] = useState(120);
  const [kronos, setKronos] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    fetcher<{ kronos: boolean }>(`${apiUrl}/api/v1/forecasts/status`)
      .then((status) => { if (active) setKronos(status.kronos); }).catch(() => {});
    return () => { active = false; };
  }, [apiUrl, fetcher]);
  async function run(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(null); setReport(null);
    try {
      setReport(await fetcher<Report>(`${apiUrl}/api/v1/forecasts`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: symbol.trim(), as_of: asOf, horizon, lookback, model }),
      }));
    } catch (e) { setError(e instanceof Error ? e.message : "無法產生預測"); }
    finally { setLoading(false); }
  }
  async function syncHistory() {
    setSyncing(true); setError(null); setSyncNote(null); setReport(null);
    const start = new Date(`${asOf}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - Math.ceil(lookback * 1.8) - 90);
    try {
      await fetcher(`${apiUrl}/api/v1/sync/symbol`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: symbol.trim(), start: start.toISOString().slice(0, 10), end: asOf }),
      });
      setSyncNote("官方日 K 同步完成，可重新產生預測。");
    } catch (e) { setError(e instanceof Error ? e.message : "歷史資料同步失敗"); }
    finally { setSyncing(false); }
  }
  return <section className="backtest-layout forecast-layout">
    <form className="backtest-form" onSubmit={run}>
      <div><p className="eyebrow">FORECAST LAB</p><h2>看見可能的路徑。</h2>
        <p>以截止日前的日 K 產生未來情境，再用歷史資料檢查統計基準的表現。</p></div>
      <label><span>預測模型</span><select value={model} onChange={e => setModel(e.target.value)}>
        <option value="bootstrap">統計基準 · 報酬率重抽樣</option>
        <option value="kronos" disabled={!kronos}>Kronos-small · {kronos ? "實驗模型" : "尚未就緒"}</option>
      </select></label>
      <label><span>預測期間</span><select value={horizon} onChange={e => setHorizon(Number(e.target.value))}>
        {[1, 5, 20].map(n => <option key={n} value={n}>{n} 個交易日</option>)}</select></label>
      <label><span>歷史觀察窗口</span><select value={lookback} onChange={e => setLookback(Number(e.target.value))}>
        {[60, 120, 250, 512].map(n => <option key={n} value={n}>{n} 根日 K</option>)}</select></label>
      <div className="cost-note">{symbol.toUpperCase()} · 截止 {asOf}<br />Kronos {kronos ? "服務已就緒" : "服務尚未連接"}；統計基準可獨立使用。</div>
      <button className="primary-button full" disabled={loading || syncing}>{loading ? "正在生成情境…" : "產生預測"}</button>
      <button className="history-sync-button" type="button" disabled={loading || syncing} onClick={syncHistory}>{syncing ? "正在同步官方日 K…" : "同步官方日 K"}</button>
      {syncNote && <p className="research-note" role="status">{syncNote}</p>}
      <p className="research-note">預測尚未用於策略回測的進出場判斷。歷史驗證與策略報酬是不同的衡量方式。</p>
    </form>
    <div className="backtest-result">
      <div className="card-heading"><span>價格情境與驗證</span><span className="job-status">{report ? (report.model === "kronos" ? "Kronos-small" : "統計基準") : "研究模式"}</span></div>
      {error && <p className="forecast-error" role="alert">{error}</p>}
      {report ? <>
        <div className="metric-grid"><div><span>最後收盤</span><strong>{price(report.last_close)}</strong></div>
          <div><span>{report.horizon} 日中位情境變動</span><strong className={report.expected_return >= 0 ? "positive" : "negative"}>{pct(report.expected_return)}</strong></div>
          <div><span>期末中位價格</span><strong>{price(report.points.at(-1)!.median)}</strong></div>
          <div><span>資料截止日</span><strong className="date-metric">{report.data_through}</strong></div></div>
        <ScenarioChart report={report} />
        <div className="chart-legend"><span className="legend-history">歷史價格</span><span className="legend-strategy">預測中位數</span><span className="legend-band">10–90% 情境區間</span></div>
        <div className="research-table-wrap"><table className="research-table"><thead><tr><th>交易日</th><th>偏低情境</th><th>中位情境</th><th>偏高情境</th></tr></thead>
          <tbody>{report.points.map(p => <tr key={p.trading_date}><td>{p.trading_date}</td><td>{price(p.lower)}</td><td>{price(p.median)}</td><td>{price(p.upper)}</td></tr>)}</tbody></table></div>
        <div className="validation-card"><p className="eyebrow">WALK-FORWARD · 統計基準模型</p>
          <h3>逐期驗證，不重看未來</h3><p>使用最多 24 個不重疊的歷史窗口。每次僅使用當時已可取得的資料，與「價格維持不變」比較。</p>
          <div className="metric-grid"><div><span>驗證窗口</span><strong>{report.evaluation.windows}</strong></div><div><span>方向命中率</span><strong>{pct(report.evaluation.direction_accuracy)}</strong></div>
            <div><span>平均絕對誤差 / 當期價格</span><strong>{pct(report.evaluation.mae_pct)}</strong></div><div><span>價格不變基準誤差</span><strong>{pct(report.evaluation.naive_mae_pct)}</strong></div></div>
          <p>80% 情境區間的歷史涵蓋率：{pct(report.evaluation.interval_coverage)}。{report.evaluation.windows < 10 && "樣本偏少，請補足歷史資料後再評估。"}</p>
          {report.model === "kronos" && <p className="forecast-error">這組驗證數據屬於統計基準，Kronos 的台股樣本外績效尚未驗證。</p>}
        </div>
        <div className="research-note">{report.warnings.map(w => <p key={w}>{w}</p>)}</div>
      </> : !loading && <div className="empty-backtest"><span>⌁</span><h3>先有預測，再看證據</h3><p>選擇模型與期間，查看未來價格情境及統計基準的歷史驗證。</p></div>}
      {loading && <div className="empty-backtest" role="status"><span>⌁</span><h3>正在計算預測路徑</h3><p>Kronos 首次運算可能需要較長時間。</p></div>}
    </div>
  </section>;
}

function ScenarioChart({ report }: { report: Report }) {
  const h = report.history;
  const values = [...h.map(p => p.close), ...report.points.flatMap(p => [p.lower, p.upper])];
  const low = Math.min(...values), high = Math.max(...values), spread = high - low || 1;
  const x = (i: number) => 65 + i / (h.length + report.points.length - 1) * 680;
  const y = (v: number) => 230 - (v - low) / spread * 190;
  const historical = h.map((p, i) => `${x(i)},${y(p.close)}`).join(" ");
  const origin = `${x(h.length - 1)},${y(h.at(-1)!.close)}`;
  const mid = [origin, ...report.points.map((p, i) => `${x(h.length + i)},${y(p.median)}`)].join(" ");
  const band = [origin, ...report.points.map((p, i) => `${x(h.length + i)},${y(p.upper)}`),
    ...report.points.map((p, i) => `${x(h.length + i)},${y(p.lower)}`).reverse(), origin].join(" ");
  return <svg className="research-chart" viewBox="0 0 780 275" role="img" aria-label={`${report.symbol} 歷史價格、預測中位數與第 10 至 90 百分位情境區間`}>
    {[0, 0.5, 1].map(t => <g key={t}><line x1="65" x2="745" y1={y(low + spread * t)} y2={y(low + spread * t)} className="chart-grid" /><text x="5" y={y(low + spread * t) + 4}>{price(low + spread * t)}</text></g>)}
    <polygon points={band} fill="rgba(91,226,194,.16)" />
    <polyline points={historical} fill="none" stroke="#adb5cd" strokeWidth="2.5" />
    <polyline points={mid} fill="none" stroke="#5be2c2" strokeWidth="2.5" strokeDasharray="5 4" />
    <line x1={x(h.length - 1)} x2={x(h.length - 1)} y1="25" y2="235" className="chart-grid" strokeDasharray="4" />
    <text x="65" y="262">{h[0].trading_date}</text><text x={x(h.length - 1) - 15} y="20">預測起點</text><text x="745" y="262" textAnchor="end">{report.points.at(-1)!.trading_date}</text>
  </svg>;
}

export function EquityChart({ strategy, buyHold }: {
  strategy: { trading_date: string; equity: number }[];
  buyHold: { trading_date: string; equity: number }[];
}) {
  if (!strategy.length) return null;
  const values = [...strategy, ...buyHold].map(p => p.equity);
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const path = (points: typeof strategy) => points.map((p, i) => `${65 + i / Math.max(1, points.length - 1) * 680},${230 - (p.equity - lo) / span * 190}`).join(" ");
  return <><svg className="research-chart" viewBox="0 0 780 275" role="img" aria-label="策略與同標的買入持有淨值比較">
    {[0, .5, 1].map(t => <g key={t}><line x1="65" x2="745" y1={230 - t * 190} y2={230 - t * 190} className="chart-grid" /><text x="5" y={234 - t * 190}>{((lo + span * t) / 10000).toFixed(1)}萬</text></g>)}
    <polyline points={path(buyHold)} fill="none" stroke="#ffca72" strokeWidth="2" />
    <polyline points={path(strategy)} fill="none" stroke="#5be2c2" strokeWidth="2.5" />
    <text x="65" y="262">{strategy[0].trading_date}</text><text x="745" y="262" textAnchor="end">{strategy.at(-1)!.trading_date}</text>
  </svg><div className="chart-legend"><span className="legend-strategy">策略淨值</span>{buyHold.length > 0 && <span className="legend-benchmark">同標的買入持有</span>}</div></>;
}
