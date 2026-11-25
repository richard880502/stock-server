"use client";

import { useState, useSyncExternalStore } from "react";
import { ChevronDownIcon } from "@/components/icons";
import { SegmentedControl } from "@/components/segmented-control";
import type { MarketMetric, MarketScope } from "@/lib/api/types";
import { METRIC_GROUPS, METRICS, SCOPES, WINDOWS } from "./metrics";

/** Matches the 719px breakpoint in market.css, below which the filters collapse. */
const NARROW_QUERY = "(max-width: 719px)";

function subscribeNarrow(onChange: () => void) {
  const query = globalThis.matchMedia(NARROW_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

const isNarrow = () => globalThis.matchMedia(NARROW_QUERY).matches;

const WINDOW_OPTIONS = WINDOWS.map((value) => ({ value, label: `${value}日` }));

export function MarketControls({
  market,
  window,
  metric,
  onMarket,
  onWindow,
  onMetric,
}: {
  market: MarketScope;
  window: number;
  metric: MarketMetric;
  onMarket: (market: MarketScope) => void;
  onWindow: (window: number) => void;
  onMetric: (metric: MarketMetric) => void;
}) {
  // Open on the server and on desktop; collapsed on phones so the rankings reach the first screen.
  // Once the user toggles it, their choice wins until the screen crosses the breakpoint again.
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, () => false);
  const [choice, setChoice] = useState<{ narrow: boolean; open: boolean } | null>(null);
  const open = choice !== null && choice.narrow === narrow ? choice.open : !narrow;
  const scopeLabel = SCOPES.find((option) => option.value === market)?.label ?? market;
  const summary = `篩選：${scopeLabel} · ${window} 日 · ${METRICS[metric].label}`;

  return (
    <details
      className="card market-controls"
      aria-label="排行條件"
      open={open}
      onToggle={(event) => setChoice({ narrow, open: event.currentTarget.open })}
    >
      <summary className="market-controls-summary">
        <span>{summary}</span>
        <ChevronDownIcon size={16} />
      </summary>
      <div className="market-controls-body">
      <div className="control-row">
        <div className="control">
          <span className="field-label">市場</span>
          <SegmentedControl label="市場" value={market} options={SCOPES} onChange={onMarket} />
        </div>
        <div className="control">
          <span className="field-label">統計期間</span>
          <SegmentedControl label="統計期間" value={window} options={WINDOW_OPTIONS} onChange={onWindow} />
        </div>
      </div>
      <div className="control">
        <span className="field-label">排行指標</span>
        <div className="metric-groups">
          {METRIC_GROUPS.map((group) => (
            <div className="metric-group" key={group.id}>
              <span className="metric-group-label" aria-hidden="true">
                {group.label}
              </span>
              <SegmentedControl
                label={`${group.label}指標`}
                wrap
                value={group.metrics.includes(metric) ? metric : ("" as MarketMetric)}
                options={group.metrics.map((value) => ({ value, label: METRICS[value].label }))}
                onChange={onMetric}
              />
            </div>
          ))}
        </div>
      </div>
      </div>
    </details>
  );
}
