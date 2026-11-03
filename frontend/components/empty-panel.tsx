import type { ReactNode } from "react";

export function EmptyPanel({
  icon,
  title,
  children,
  live,
}: {
  icon: string;
  title: string;
  children: ReactNode;
  /** Announce the panel to screen readers (used for loading states). */
  live?: boolean;
}) {
  return (
    <div className="empty-backtest" role={live ? "status" : undefined}>
      <span aria-hidden="true">{icon}</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
