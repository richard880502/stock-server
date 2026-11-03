import type { ReactNode } from "react";

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="metric-grid">{children}</div>;
}

export function Metric({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: ReactNode;
  valueClassName?: string;
}) {
  return (
    <div>
      <span>{label}</span>
      <strong className={valueClassName}>{value}</strong>
    </div>
  );
}
