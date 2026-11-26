"use client";

import { useState } from "react";
import type { SectorRow } from "@/lib/api/types";
import { formatNumber } from "@/lib/format";
import { QUADRANTS, rrgDomain } from "./sector-math";

const WIDTH = 520;
const HEIGHT = 380;
const PAD = { left: 40, right: 16, top: 16, bottom: 36 };
const LABELLED = 8;

/**
 * Relative-rotation chart: x = RS ratio (strength vs the whole market), y = RS momentum,
 * both centred on 100. A sector normally travels 領先 → 轉弱 → 落後 → 改善, clockwise.
 * Plain SVG: lightweight-charts has no scatter series.
 */
export function SectorRrgChart({
  rows,
  selected,
  onSelect,
}: {
  rows: SectorRow[];
  selected: string | null;
  onSelect: (code: string) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const plotted = rows.filter((row) => row.rrg !== null);
  const { half } = rrgDomain(plotted);
  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const x = (ratio: number) => PAD.left + ((ratio - (100 - half)) / (2 * half)) * innerW;
  const y = (momentum: number) => PAD.top + (1 - (momentum - (100 - half)) / (2 * half)) * innerH;
  const cx = x(100);
  const cy = y(100);

  // Name the points that matter: the selected one, the hovered one and the most extreme few.
  const distance = (row: SectorRow) =>
    Math.hypot((row.rrg?.rs_ratio ?? 100) - 100, (row.rrg?.rs_momentum ?? 100) - 100);
  const labelled = new Set(
    [...plotted]
      .sort((a, b) => distance(b) - distance(a))
      .slice(0, LABELLED)
      .map((row) => row.code),
  );
  if (selected) labelled.add(selected);
  if (hover) labelled.add(hover);

  if (plotted.length === 0) {
    return <p className="rrg-empty">需要約 15 個交易日的資料才畫得出相對強弱象限圖。</p>;
  }

  return (
    <figure className="rrg">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-label="族群相對強弱與動能象限圖"
        className="rrg-svg"
      >
        <rect className="rrg-q q-leading" x={cx} y={PAD.top} width={WIDTH - PAD.right - cx} height={cy - PAD.top} />
        <rect className="rrg-q q-weakening" x={cx} y={cy} width={WIDTH - PAD.right - cx} height={HEIGHT - PAD.bottom - cy} />
        <rect className="rrg-q q-lagging" x={PAD.left} y={cy} width={cx - PAD.left} height={HEIGHT - PAD.bottom - cy} />
        <rect className="rrg-q q-improving" x={PAD.left} y={PAD.top} width={cx - PAD.left} height={cy - PAD.top} />
        <line className="rrg-axis" x1={cx} x2={cx} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
        <line className="rrg-axis" x1={PAD.left} x2={WIDTH - PAD.right} y1={cy} y2={cy} />
        <text className="rrg-corner" x={WIDTH - PAD.right - 6} y={PAD.top + 14} textAnchor="end">
          {QUADRANTS.leading.label}
        </text>
        <text className="rrg-corner" x={WIDTH - PAD.right - 6} y={HEIGHT - PAD.bottom - 8} textAnchor="end">
          {QUADRANTS.weakening.label}
        </text>
        <text className="rrg-corner" x={PAD.left + 6} y={HEIGHT - PAD.bottom - 8}>
          {QUADRANTS.lagging.label}
        </text>
        <text className="rrg-corner" x={PAD.left + 6} y={PAD.top + 14}>
          {QUADRANTS.improving.label}
        </text>
        <text className="rrg-axis-label" x={PAD.left + innerW / 2} y={HEIGHT - 8} textAnchor="middle">
          相對強弱（對大盤）→
        </text>
        <text
          className="rrg-axis-label"
          transform={`translate(12 ${PAD.top + innerH / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          動能 →
        </text>
        {plotted.map((row) => {
          const rrg = row.rrg!;
          const active = row.code === selected;
          const trail = [...rrg.trail.slice(0, -1), rrg];
          return (
            <g key={row.code} className={`rrg-point q-${rrg.quadrant}${active ? " is-active" : ""}`}>
              {trail.length > 1 && (
                <polyline
                  className="rrg-trail"
                  points={trail.map((p) => `${x(p.rs_ratio).toFixed(1)},${y(p.rs_momentum).toFixed(1)}`).join(" ")}
                />
              )}
              <g
                role="button"
                tabIndex={0}
                aria-pressed={active}
                aria-label={`${row.name}，${QUADRANTS[rrg.quadrant].label}，相對強弱 ${formatNumber(rrg.rs_ratio, 1)}，動能 ${formatNumber(rrg.rs_momentum, 1)}`}
                onClick={() => onSelect(row.code)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(row.code);
                  }
                }}
                onMouseEnter={() => setHover(row.code)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(row.code)}
                onBlur={() => setHover(null)}
              >
                <title>{`${row.name}（${QUADRANTS[rrg.quadrant].label}）`}</title>
                <circle className="rrg-hit" cx={x(rrg.rs_ratio)} cy={y(rrg.rs_momentum)} r={12} />
                <circle className="rrg-dot" cx={x(rrg.rs_ratio)} cy={y(rrg.rs_momentum)} r={active ? 6 : 4.5} />
                {labelled.has(row.code) && (
                  <text className="rrg-name" x={x(rrg.rs_ratio) + 8} y={y(rrg.rs_momentum) + 4}>
                    {row.name}
                  </text>
                )}
              </g>
            </g>
          );
        })}
      </svg>
      <figcaption className="rrg-legend">
        {(Object.keys(QUADRANTS) as (keyof typeof QUADRANTS)[]).map((key) => (
          <span key={key} className={`rrg-legend-item q-${key}`}>
            <i aria-hidden="true" />
            {QUADRANTS[key].label}
            <small>{QUADRANTS[key].hint}</small>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
