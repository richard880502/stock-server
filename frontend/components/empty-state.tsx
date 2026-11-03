import type { ReactNode } from "react";

/** Calm placeholder: what is missing, why, and (optionally) the next step. */
export function EmptyState({
  icon,
  title,
  children,
  action,
  live,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  /** Announce to screen readers (loading / progress states). */
  live?: boolean;
}) {
  return (
    <div className="empty-state" role={live ? "status" : undefined}>
      <span className="empty-state-icon" aria-hidden="true">
        {icon}
      </span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
