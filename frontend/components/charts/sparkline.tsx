/** Tiny inline trend line. Colour follows direction (red up / green down, Taiwan convention). */
export function Sparkline({
  values,
  width = 96,
  height = 28,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  label: string;
}) {
  if (values.length < 2) return <span className="sparkline-empty">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 2;
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const first = values[0];
  const last = values[values.length - 1];
  const tone = last > first ? "up" : last < first ? "down" : "flat";
  return (
    <svg
      className={`sparkline tone-${tone}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      <line className="sparkline-base" x1={pad} x2={width - pad} y1={y(first)} y2={y(first)} />
      <polyline points={points} fill="none" />
      <circle cx={x(values.length - 1)} cy={y(last)} r={2} />
    </svg>
  );
}
