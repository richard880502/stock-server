"use client";

import { SegmentedControl } from "@/components/segmented-control";
import type { MarketMetric, MarketScope } from "@/lib/api/types";
import { METRIC_GROUPS, METRICS, SCOPES, WINDOWS } from "./metrics";

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
  return (
    <section className="card market-controls" aria-label="排行條件">
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
    </section>
  );
}
