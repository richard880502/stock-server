"use client";

import Link from "next/link";
import { RegimeChip } from "@/components/status-chip";
import type { ChipFlow, MarketEnvironment, Signal } from "@/lib/api/types";
import { actionCopy, actionTone, severityCopy } from "@/lib/copy";
import { directionOf, formatNumber, formatPct, formatPrice, formatSignedPct } from "@/lib/format";
import { changeSinceGeneration } from "./ai-price";
import { collectAlerts, topFactors } from "./ai-signal";

export const AI_DISCLAIMER =
  "AI 內容是模型產生的文字，不構成投資建議；模型只整理已有證據，可能出錯。";

/** Every AI suggestion area ends here: numbers must be checked by the deterministic backtest. */
export function BacktestLink({ label = "用回測驗證" }: { label?: string }) {
  return (
    <div className="ai-verify">
      <Link href="/backtest" className="button small">
        {label}
      </Link>
      <span>AI 只提出觀點，數字策略要經回測檢驗。</span>
    </div>
  );
}

/** 「產生後 +x.xx%」 plus the note that the AI saw daily data while the price is live. */
export function PriceDrift({
  generatedPrice,
  livePrice,
  dataAsOf,
}: {
  generatedPrice: number | null;
  livePrice: number | undefined;
  dataAsOf: string | undefined;
}) {
  const change = changeSinceGeneration(generatedPrice, livePrice);
  if (change === null || generatedPrice === null || livePrice === undefined) return null;
  const tone = directionOf(change);
  return (
    <div className="ai-drift">
      <p>
        <span>產生後</span>{" "}
        <strong className={`num ai-tone-${tone}`}>{formatSignedPct(change, 2)}</strong>{" "}
        <span className="ai-drift-detail num">
          （{formatPrice(generatedPrice)} → {formatPrice(livePrice)}）
        </span>
      </p>
      <p className="ai-note">
        AI 使用截至 {dataAsOf ?? "訊號日"} 的日資料判讀，現價為即時報價，兩者時間不同。
      </p>
    </div>
  );
}

/** Deterministic section: free, recomputed whenever the symbol changes. */
export function SignalSection({
  signal,
  loading,
  syncing,
  marketEnvironment,
  chipFlow,
}: {
  signal: Signal | null;
  loading: boolean;
  syncing: boolean;
  marketEnvironment: MarketEnvironment | null;
  chipFlow: ChipFlow | null;
}) {
  if (!signal) {
    return (
      <section className="ai-section" aria-labelledby="ai-signal-title" aria-busy={loading}>
        <h3 id="ai-signal-title" className="ai-section-title">
          量化訊號
        </h3>
        {loading || syncing ? (
          <div className="ai-skeleton" role="status" aria-label="正在計算量化訊號">
            <span className="skeleton line" style={{ width: "50%" }} />
            <span className="skeleton line" />
            <span className="skeleton line" style={{ width: "80%" }} />
          </div>
        ) : (
          <p className="ai-empty">目前沒有這檔的量化訊號。</p>
        )}
      </section>
    );
  }

  const tone = actionTone[signal.action];
  const factors = topFactors(signal.factors, 3);
  const alerts = collectAlerts(signal, chipFlow, marketEnvironment);
  const shown = alerts.slice(0, 3);
  const rest = alerts.slice(3);
  const score = Math.round(signal.score);

  const alertItem = (alert: (typeof alerts)[number]) => (
    <li key={`${alert.source}:${alert.code}`} className={`ai-alert sev-${alert.severity}`}>
      <span className="ai-alert-sev">{severityCopy[alert.severity]}</span>
      <span>{alert.title}</span>
    </li>
  );

  return (
    <section className="ai-section" aria-labelledby="ai-signal-title">
      <h3 id="ai-signal-title" className="ai-section-title">
        量化訊號
        <small className="num">截止 {signal.as_of}</small>
      </h3>
      <div className="ai-signal-head">
        <strong className={`ai-action ai-tone-${tone}`}>{actionCopy[signal.action].label}</strong>
        <RegimeChip regime={signal.regime} />
      </div>
      <dl className="ai-stats">
        <div>
          <dt>分數</dt>
          <dd className="num">
            {score}
            <small> / 100</small>
          </dd>
        </div>
        <div>
          <dt>信心度</dt>
          <dd className="num">{formatPct(signal.confidence)}</dd>
        </div>
      </dl>
      <ul className="ai-factors" aria-label="得分最高的三項因子">
        {factors.map((factor) => (
          <li key={factor.key}>
            <span className="ai-factor-label">{factor.label}</span>
            <span className="ai-factor-track" aria-hidden="true">
              <span style={{ width: `${Math.max(2, Math.min(100, factor.value))}%` }} />
            </span>
            <span className="num ai-factor-value">{formatNumber(factor.value, 0)}</span>
          </li>
        ))}
      </ul>
      {alerts.length > 0 ? (
        <>
          <ul className="ai-alerts" aria-label="異常提示">
            {shown.map(alertItem)}
          </ul>
          {rest.length > 0 && (
            <details className="ai-more">
              <summary>其他 {rest.length} 項提示</summary>
              <ul className="ai-alerts">{rest.map(alertItem)}</ul>
            </details>
          )}
        </>
      ) : (
        <p className="ai-empty">未偵測到重大異常。</p>
      )}
    </section>
  );
}
