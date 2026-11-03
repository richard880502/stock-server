import type { CSSProperties } from "react";
import { CardHeading } from "@/components/card-heading";
import { RegimeChip } from "@/components/status-chip";
import type { Alert, Signal } from "@/lib/api/types";
import { actionCopy, factorLabels } from "@/lib/copy";
import { formatNumber, formatPct } from "@/lib/format";

export function ScoreCard({ signal }: { signal: Signal | null }) {
  const action = signal ? actionCopy[signal.action] : null;
  return (
    <article className="signal-card score-card">
      <CardHeading title="個股綜合訊號" aside={<RegimeChip regime={signal?.regime} />} />
      {signal ? (
        <>
          <div className="score-block">
            <div
              className="score-ring"
              role="img"
              aria-label={`綜合分數 ${Math.round(signal.score)}，滿分 100`}
              style={{ "--score": `${signal.score * 3.6}deg` } as CSSProperties}
            >
              <div aria-hidden="true">
                <strong>{Math.round(signal.score)}</strong>
                <span>/ 100</span>
              </div>
            </div>
            <div className="score-copy">
              <p>{signal.symbol}</p>
              <h2>{action?.label}</h2>
              <span>
                信心度 {formatPct(signal.confidence)} · {signal.observations} 筆觀察
              </span>
            </div>
          </div>
          <div className="quant-guide">
            <span>量化操作框架</span>
            <p>{action?.guide}</p>
          </div>
        </>
      ) : (
        <div className="empty-state">輸入標的後取得訊號</div>
      )}
    </article>
  );
}

export function FactorsCard({ signal }: { signal: Signal | null }) {
  return (
    <article className="signal-card factors-card">
      <CardHeading title="因子證據" aside={<small>regime_v1</small>} />
      <div className="factor-list">
        {signal
          ? (Object.entries(signal.factors) as [keyof Signal["factors"], number][]).map(
              ([key, value]) => (
                <div className="factor-row" key={key}>
                  <div>
                    <span>{factorLabels[key]}</span>
                    <strong>{formatNumber(value, 0)}</strong>
                  </div>
                  <div className="factor-track" aria-hidden="true">
                    <span style={{ width: `${value}%` }} />
                  </div>
                </div>
              ),
            )
          : Object.values(factorLabels).map((label) => (
              <div className="factor-row muted" key={label}>
                <div>
                  <span>{label}</span>
                  <strong>—</strong>
                </div>
                <div className="factor-track" aria-hidden="true" />
              </div>
            ))}
      </div>
    </article>
  );
}

export function AlertsCard({ alerts }: { alerts: Alert[] }) {
  return (
    <article className="signal-card alerts-card">
      <CardHeading title="異常雷達" aside={<span className="alert-count">{alerts.length}</span>} />
      {alerts.length ? (
        <div className="alert-list">
          {alerts.map((alert) => (
            <div className={`alert-item ${alert.severity}`} key={alert.code}>
              <span className="alert-indicator" aria-hidden="true" />
              <div>
                <strong>{alert.title}</strong>
                <p>
                  {Object.entries(alert.evidence)
                    .map(([key, value]) => `${key}: ${value}`)
                    .join(" · ")}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="clear-state">
          <span aria-hidden="true">✓</span>
          <div>
            <strong>未偵測到重大異常</strong>
            <p>市場寬度、資金流與個股量價目前都在可接受範圍。</p>
          </div>
        </div>
      )}
    </article>
  );
}
