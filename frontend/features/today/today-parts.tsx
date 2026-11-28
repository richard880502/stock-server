import Link from "next/link";
import type { ReactNode } from "react";

/** Per-card failure with its own retry; never blocks the neighbouring cards. */
export function CardError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <p className="today-error note" role="alert">
      <span>{message}</span>
      <button type="button" className="button small" onClick={onRetry}>
        重試
      </button>
    </p>
  );
}

/** Reserved-height loading block of a card body. */
export function CardSkeleton({ label, lines = 3 }: { label: string; lines?: number }) {
  return (
    <div className="today-skeleton" aria-busy="true" aria-label={label} role="status">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton line" style={{ width: `${92 - index * 14}%` }} />
      ))}
    </div>
  );
}

export function MoreLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="today-more">
      {children}
    </Link>
  );
}
