import type { ReactNode } from "react";

export function StatGrid({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`stat-grid ${className}`.trim()}>{children}</div>;
}

export function Stat({
  label,
  value,
  note,
  valueClassName = "",
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <strong className={`stat-value ${valueClassName}`.trim()}>{value}</strong>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}
