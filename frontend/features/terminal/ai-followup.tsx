"use client";

import { useEffect, useRef, useState } from "react";
import { askStrategyQuestion } from "@/lib/api/endpoints";
import type { DebateReport } from "@/lib/api/types";
import { aiCache, type AiMessage } from "./ai-cache";
import { BacktestLink } from "./ai-parts";

const SHORTCUTS = ["什麼情況會讓觀點失效？", "該觀察哪些條件？"];

/**
 * Chat-style follow-up questions about the finished debate (the API needs the report,
 * so this stays disabled until one exists). Mount it with `key` = cache key so a symbol
 * change aborts any request in flight and resets the draft.
 */
export function AiFollowUp({
  cacheKey,
  report,
  messages,
}: {
  cacheKey: string;
  report: DebateReport | null;
  messages: AiMessage[];
}) {
  const [question, setQuestion] = useState("");
  const [held, setHeld] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !report || busy) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await askStrategyQuestion(
        report,
        trimmed,
        held,
        { signal: controller.signal },
        messages.slice(-2),
      );
      if (controller.signal.aborted) return;
      aiCache.addMessage(cacheKey, { question: trimmed, answer: result.answer });
      setQuestion("");
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : "提問失敗，請再試一次。");
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const disabled = !report;

  return (
    <section className="ai-section" aria-labelledby="ai-followup-title">
      <h3 id="ai-followup-title" className="ai-section-title">
        策略追問
      </h3>
      {disabled && <p className="ai-empty">先完成「多空辯論」，才能針對那份策略追問。</p>}
      <div className="ai-chips" role="group" aria-label="快速提問">
        {SHORTCUTS.map((text) => (
          <button
            key={text}
            type="button"
            className="ai-chip"
            disabled={disabled || busy}
            onClick={() => void ask(text)}
          >
            {text}
          </button>
        ))}
      </div>
      {messages.length > 0 && (
        <div className="ai-thread" aria-live="polite">
          {messages.map((message, index) => (
            <div className="ai-message" key={index}>
              <p className="ai-question">{message.question}</p>
              <p className="ai-answer">{message.answer}</p>
              <BacktestLink />
            </div>
          ))}
        </div>
      )}
      {error && (
        <p className="ai-error" role="alert">
          {error}
        </p>
      )}
      <form
        className="ai-ask"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
      >
        <label htmlFor="ai-question" className="sr-only">
          輸入你的問題
        </label>
        <textarea
          id="ai-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={2}
          maxLength={1000}
          disabled={disabled}
          placeholder={disabled ? "尚無辯論報告" : "例如：哪些證據值得持續追蹤？"}
        />
        <div className="ai-ask-row">
          <label className="ai-held">
            <input
              type="checkbox"
              checked={held}
              disabled={disabled}
              onChange={(event) => setHeld(event.target.checked)}
            />
            我已持有
          </label>
          <button
            type="submit"
            className="button small"
            disabled={disabled || busy || !question.trim()}
          >
            {busy ? "回覆中…" : "送出"}
          </button>
        </div>
      </form>
    </section>
  );
}
