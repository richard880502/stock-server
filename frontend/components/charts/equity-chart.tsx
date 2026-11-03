type Point = { trading_date: string; equity: number };

export function EquityChart({
  strategy,
  buyHold,
}: {
  strategy: Point[];
  buyHold: Point[];
}) {
  if (!strategy.length) return null;
  const values = [...strategy, ...buyHold].map((p) => p.equity);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const path = (points: Point[]) =>
    points
      .map(
        (p, i) =>
          `${65 + (i / Math.max(1, points.length - 1)) * 680},${230 - ((p.equity - lo) / span) * 190}`,
      )
      .join(" ");
  return (
    <>
      <svg
        className="research-chart"
        viewBox="0 0 780 275"
        role="img"
        aria-label="策略與同標的買入持有淨值比較"
      >
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              x1="65"
              x2="745"
              y1={230 - t * 190}
              y2={230 - t * 190}
              className="chart-grid"
            />
            <text x="5" y={234 - t * 190}>
              {((lo + span * t) / 10000).toFixed(1)}萬
            </text>
          </g>
        ))}
        <polyline points={path(buyHold)} fill="none" stroke="#ffca72" strokeWidth="2" />
        <polyline points={path(strategy)} fill="none" stroke="#5be2c2" strokeWidth="2.5" />
        <text x="65" y="262">
          {strategy[0].trading_date}
        </text>
        <text x="745" y="262" textAnchor="end">
          {strategy[strategy.length - 1].trading_date}
        </text>
      </svg>
      <div className="chart-legend">
        <span className="legend-strategy">策略淨值</span>
        {buyHold.length > 0 && <span className="legend-benchmark">同標的買入持有</span>}
      </div>
    </>
  );
}
