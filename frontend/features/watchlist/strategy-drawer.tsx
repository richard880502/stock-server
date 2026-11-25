"use client";

import { useEffect, useRef, useState } from "react";
import { askStrategyQuestion, createDebateAnalysis } from "@/lib/api/endpoints";
import type { WatchlistItem } from "@/lib/api/types";
import { today } from "@/lib/format";
import { benchmarkForSymbol, DEFAULT_BENCHMARK } from "@/lib/symbols";
import type { StrategyEntry } from "./strategy-entry";
import "./drawer.css";

type Props = {
  item: WatchlistItem;
  entry: StrategyEntry;
  update: (patch: Partial<StrategyEntry>) => void;
  onClose: () => void;
  onBacktest: (asOf: string) => void;
};

const stanceLabels: Record<string, string> = { bullish: "偏多", neutral: "中立", cautious: "審慎", bearish: "偏空" };

export function StrategyDrawer({ item, entry, update, onClose, onBacktest }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const report = entry.report;

  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    return () => {
      request.current?.abort();
      element?.close();
      previousFocus?.focus();
    };
  }, []);

  const generate = async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await createDebateAnalysis({ symbol: item.symbol, as_of: today(), market: item.market, benchmark: benchmarkForSymbol(item.symbol, DEFAULT_BENCHMARK), strategy_version: "regime_v1", backtest_job_id: null }, { signal: controller.signal });
      if (!controller.signal.aborted) update({ previous: report, report: result, messages: [], dataThrough: item.data_through, flowsThrough: item.flows_through });
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "策略分析失敗，請再試一次。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !report || busy) return;
    if (trimmed === "和上次分析有什麼不同？") {
      const prior = entry.previous;
      const answer = prior
        ? `上次（${prior.as_of || "日期未提供"}）：${stanceLabels[prior.judgement.stance] || prior.judgement.stance}，${prior.judgement.headline}。本次（${report.as_of || "日期未提供"}）：${stanceLabels[report.judgement.stance] || report.judgement.stance}，${report.judgement.headline}。`
        : "目前沒有上一次分析可供比較。重新分析後可比較兩次結論。";
      update({ messages: [...entry.messages, { question: trimmed, answer }] });
      setQuestion("");
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await askStrategyQuestion(report, trimmed, entry.held, { signal: controller.signal }, entry.messages.slice(-2));
      if (!controller.signal.aborted) {
        update({ messages: [...entry.messages, { question: trimmed, answer: result.answer }] });
        setQuestion("");
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "提問失敗，請再試一次。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return (
    <dialog ref={dialog} className="strategy-drawer" aria-labelledby="strategy-title" onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="strategy-drawer-inner">
        <header className="strategy-header">
          <div><p className="strategy-eyebrow">個股策略研究</p><h2 id="strategy-title">{item.name || item.symbol} <span>{item.symbol.replace(/\.TWO?$/, "")}</span></h2></div>
          <button type="button" className="strategy-close" onClick={onClose} aria-label="關閉策略研究">×</button>
        </header>
        <div className="strategy-content">
          <fieldset className="strategy-position"><legend>你的關注方式</legend><div>
            <label><input type="radio" name="strategy-held" checked={entry.held} onChange={() => update({ held: true })} /> 已持有</label>
            <label><input type="radio" name="strategy-held" checked={!entry.held} onChange={() => update({ held: false })} /> 追蹤中</label>
          </div><p>{entry.held ? "從持有風險、應對情境與失效條件檢視。" : "從觀察條件、進場風險與研究證據檢視。"}</p></fieldset>
          <section className="strategy-section" aria-labelledby="strategy-overview"><div className="strategy-section-heading"><h3 id="strategy-overview">策略觀點</h3><button type="button" className="button" disabled={busy} onClick={generate}>{busy ? "處理中…" : report ? "重新分析" : "產生策略分析"}</button></div>
            {!report ? <p className="strategy-muted">依最新可用資料產生多空觀點與風險研究。</p> : <>
              <p className="strategy-dates">分析日期：{report.as_of || "未提供"} · 行情截至：{entry.dataThrough || "未提供"} · 法人資料截至：{entry.flowsThrough || "未提供"}</p>
              {report.data_sufficient === false && <p className="strategy-warning">資料不足，請審慎解讀分析。</p>}
              <div className="strategy-verdict"><span>{stanceLabels[report.judgement.stance] || report.judgement.stance}</span><h4>{report.judgement.headline}</h4><p>{report.judgement.operation_guide}</p>
                {entry.held ? <p><strong>持有時留意的失效條件：</strong>{report.risk_review?.invalidation_conditions[0] || "本次分析未提供明確失效條件。"}</p> : <p><strong>下一個觀察情境：</strong>{report.judgement.scenarios[0] ? `${report.judgement.scenarios[0].name}：${report.judgement.scenarios[0].condition}；${report.judgement.scenarios[0].response}` : "本次分析未提供明確情境。"}</p>}
              </div>
              <div className="strategy-columns"><div><h4>支持觀點</h4><p>{report.bull_case.thesis}</p><ul>{report.bull_case.key_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></div><div><h4>反方觀點</h4><p>{report.bear_case.thesis}</p><ul>{report.bear_case.key_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></div></div>
              <h4>關鍵證據</h4><ul>{report.judgement.key_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul>
              <h4>風險與失效條件</h4><ul>{[...report.judgement.risk_warnings, ...(report.risk_review?.risk_warnings || []), ...(report.risk_review?.invalidation_conditions || [])].map((x, i) => <li key={i}>{x}</li>)}</ul>
              {report.judgement.scenarios.length > 0 && <><h4>情境與應對</h4>{report.judgement.scenarios.map((s, i) => <p key={i}><strong>{s.name}</strong>：{s.condition}；{s.response}</p>)}</>}
              {report.judgement.limitations.length > 0 && <><h4>分析限制</h4><ul>{report.judgement.limitations.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
              {!!report.evidence?.length && <details><summary>查看資料證據</summary>{report.evidence.map((e, i) => <details key={`${e.id}-${i}`}><summary>{e.id} · {e.source} · {e.as_of}</summary><pre>{JSON.stringify(e.details, null, 2)}</pre></details>)}</details>}
            </>}
          </section>
          {report && <section className="strategy-section" aria-labelledby="strategy-chat"><h3 id="strategy-chat">追問策略</h3><div className="strategy-shortcuts">{[entry.held ? "持有時應注意哪些風險？" : "追蹤時應觀察哪些條件？", "什麼情況會讓觀點失效？", "和上次分析有什麼不同？"].map((q) => <button type="button" key={q} disabled={busy} onClick={() => ask(q)}>{q}</button>)}</div>
            <div aria-live="polite">{entry.messages.map((m, i) => <div className="strategy-message" key={i}><strong>{m.question}</strong><p>{m.answer}</p></div>)}</div>
            <form onSubmit={(event) => { event.preventDefault(); void ask(question); }}><label htmlFor="strategy-question">輸入你的問題</label><textarea id="strategy-question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} maxLength={1000} placeholder="例如：哪些證據值得持續追蹤？" /><button className="button" type="submit" disabled={busy || !question.trim()}>送出問題</button></form>
          </section>}
          {error && <p className="strategy-error" role="alert">{error}</p>}
          <section className="strategy-section strategy-backtest"><h3>回測驗證</h3><p>查看歷史策略表現，並留意過去表現不代表未來結果。</p><button type="button" className="button" onClick={() => onBacktest(report?.as_of || today())}>前往回測</button></section>
        </div>
      </div>
    </dialog>
  );
}
