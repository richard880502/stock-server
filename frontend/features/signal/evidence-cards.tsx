"use client";

import { useState } from "react";
import { Card } from "@/components/card";
import { AlertIcon, CheckIcon, InfoIcon } from "@/components/icons";
import type { Alert, Signal } from "@/lib/api/types";
import { factorLabels, severityCopy } from "@/lib/copy";
import { formatNumber } from "@/lib/format";

export function FactorsCard({ signal }: { signal: Signal }) {
  const rows = Object.entries(signal.factors) as [keyof Signal["factors"], number][];
  return (
    <Card title="因子分數" sub={`0–100 分，策略 ${signal.strategy_version}`} className="factors-card">
      <ul className="factor-list">
        {rows.map(([key, value]) => (
          <li className="factor-row" key={key}>
            <span className="factor-label">{factorLabels[key]}</span>
            <div
              className="factor-track"
              role="img"
              aria-label={`${factorLabels[key]} ${formatNumber(value, 0)} 分，滿分 100`}
            >
              <span className="factor-tick" />
              <span className="factor-fill" style={{ width: `${Math.max(1.5, value)}%` }} />
            </div>
            <span className="factor-value num">{formatNumber(value, 0)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export type SourcedAlert = Alert & { source: "market" | "symbol" | "chip" };

const sourceCopy = { market: "市場", symbol: "個股", chip: "籌碼" } as const;
const severityRank = { high: 0, medium: 1, info: 2 } as const;
const PREVIEW = 4;

function SeverityBadge({ severity }: { severity: Alert["severity"] }) {
  const Icon = severity === "info" ? InfoIcon : AlertIcon;
  return (
    <span className={`severity severity-${severity}`}>
      <Icon size={14} />
      {severityCopy[severity]}
    </span>
  );
}

export function AlertsCard({ alerts }: { alerts: SourcedAlert[] }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = [...alerts].sort((a, b) => severityRank[a.severity] - severityRank[b.severity]);
  const visible = expanded ? sorted : sorted.slice(0, PREVIEW);
  return (
    <Card
      title="異常提示"
      sub={alerts.length ? `${alerts.length} 項，依嚴重度排序` : undefined}
      aside={alerts.length > 0 && <span className="chip plain num">{alerts.length}</span>}
      className="alerts-card"
    >
      {alerts.length ? (
        <>
          <ul className="alert-list">
            {visible.map((alert) => (
              <li className="alert-item" key={`${alert.source}:${alert.code}`}>
                <SeverityBadge severity={alert.severity} />
                <div className="alert-body">
                  <p className="alert-title">
                    <span className="alert-source">{sourceCopy[alert.source]}</span>
                    {alert.title}
                  </p>
                  <p className="alert-evidence mono">
                    {Object.entries(alert.evidence)
                      .map(([key, value]) => `${key} ${value}`)
                      .join(" · ")}
                  </p>
                </div>
              </li>
            ))}
          </ul>
          {sorted.length > PREVIEW && (
            <button
              type="button"
              className="button link alert-more"
              onClick={() => setExpanded((open) => !open)}
              aria-expanded={expanded}
            >
              {expanded ? "收合" : `顯示全部 ${sorted.length} 項`}
            </button>
          )}
        </>
      ) : (
        <div className="clear-state">
          <span className="clear-icon" aria-hidden="true">
            <CheckIcon size={18} />
          </span>
          <div>
            <strong>未偵測到重大異常</strong>
            <p className="note">市場寬度、資金流與個股量價目前都在模型的正常範圍。</p>
          </div>
        </div>
      )}
    </Card>
  );
}
