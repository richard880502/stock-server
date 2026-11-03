import type { ForecastReport } from "@/lib/api/types";
import { formatPrice } from "@/lib/format";

export function ScenarioChart({ report }: { report: ForecastReport }) {
  const h = report.history;
  const lastPoint = report.points[report.points.length - 1];
  const lastHistory = h[h.length - 1];
  const values = [
    ...h.map((p) => p.close),
    ...report.points.flatMap((p) => [p.lower, p.upper]),
  ];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const spread = high - low || 1;
  const x = (i: number) => 65 + (i / (h.length + report.points.length - 1)) * 680;
  const y = (v: number) => 230 - ((v - low) / spread) * 190;
  const historical = h.map((p, i) => `${x(i)},${y(p.close)}`).join(" ");
  const origin = `${x(h.length - 1)},${y(lastHistory.close)}`;
  const mid = [
    origin,
    ...report.points.map((p, i) => `${x(h.length + i)},${y(p.median)}`),
  ].join(" ");
  const band = [
    origin,
    ...report.points.map((p, i) => `${x(h.length + i)},${y(p.upper)}`),
    ...report.points.map((p, i) => `${x(h.length + i)},${y(p.lower)}`).reverse(),
    origin,
  ].join(" ");
  return (
    <svg
      className="research-chart"
      viewBox="0 0 780 275"
      role="img"
      aria-label={`${report.symbol} 歷史價格、預測中位數與第 10 至 90 百分位情境區間`}
    >
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line
            x1="65"
            x2="745"
            y1={y(low + spread * t)}
            y2={y(low + spread * t)}
            className="chart-grid"
          />
          <text x="5" y={y(low + spread * t) + 4}>
            {formatPrice(low + spread * t)}
          </text>
        </g>
      ))}
      <polygon points={band} fill="rgba(91,226,194,.16)" />
      <polyline points={historical} fill="none" stroke="#adb5cd" strokeWidth="2.5" />
      <polyline
        points={mid}
        fill="none"
        stroke="#5be2c2"
        strokeWidth="2.5"
        strokeDasharray="5 4"
      />
      <line
        x1={x(h.length - 1)}
        x2={x(h.length - 1)}
        y1="25"
        y2="235"
        className="chart-grid"
        strokeDasharray="4"
      />
      <text x="65" y="262">
        {h[0].trading_date}
      </text>
      <text x={x(h.length - 1) - 15} y="20">
        預測起點
      </text>
      <text x="745" y="262" textAnchor="end">
        {lastPoint.trading_date}
      </text>
    </svg>
  );
}
