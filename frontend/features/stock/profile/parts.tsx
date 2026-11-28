"use client";

import type { ReactNode } from "react";
import { InfoIcon } from "@/components/icons";
import { percentileReason, percentileText } from "./model";

export function PanelSkeleton({ label }: { label: string }) {
  return (
    <div className="pf-skeleton" role="status" aria-label={`${label}載入中`}>
      <div className="pf-tiles" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className="skeleton block" style={{ height: 76 }} />
        ))}
      </div>
      <span className="skeleton block" aria-hidden="true" style={{ height: 190 }} />
    </div>
  );
}

export function PanelError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="pf-error" role="alert">
      <p>
        <strong>這一區暫時讀不到。</strong> {message}
      </p>
      <button type="button" className="button small" onClick={onRetry}>
        重試
      </button>
    </div>
  );
}

/** One quiet line saying why a section has nothing to show. */
export function EmptyLine({ children }: { children: ReactNode }) {
  return (
    <p className="pf-empty">
      <InfoIcon size={16} />
      <span>{children}</span>
    </p>
  );
}

export function Tile({
  label,
  value,
  note,
  tone = "",
  children,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: string;
  children?: ReactNode;
}) {
  return (
    <div className="pf-tile">
      <span className="stat-label">{label}</span>
      <strong className={`stat-value ${tone}`.trim()}>{value}</strong>
      {note && <span className="stat-note">{note}</span>}
      {children}
    </div>
  );
}

export function TileGrid({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={`pf-tiles${wide ? " wide" : ""}`}>{children}</div>;
}

/** 0-100 bar with its wording, or the reason there is none. */
export function PercentileBar({
  value,
  percentile,
  label,
  progress,
}: {
  value: number | null;
  percentile: number | null;
  label: string;
  /** Days of history stored vs. needed, for the "still accumulating" line. */
  progress?: { observations: number; required: number } | null;
}) {
  const text = percentileText(percentile);
  if (text === null || percentile === null) {
    return <span className="pf-percentile-reason">{percentileReason(value, percentile, progress)}</span>;
  }
  const pct = Math.min(100, Math.max(0, percentile));
  return (
    <span className="pf-percentile">
      <span
        className="pf-bar"
        role="meter"
        aria-label={`${label}分位`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-valuetext={text}
      >
        <span className="pf-bar-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="pf-percentile-text">{text}</span>
    </span>
  );
}

/** Server notes, tucked away: the one-line reasons live next to the data, the fine print here. */
export function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <details className="pf-notes">
      <summary>資料說明</summary>
      <ul>
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </details>
  );
}

export function DataDate({ children }: { children: ReactNode }) {
  return <p className="pf-date num">{children}</p>;
}

type Resource<T> = { data: T | null; error: string | null; loading: boolean; retry: () => void };

/** Skeleton while loading, retryable error, then the tab body. */
export function Boundary<T>({
  resource,
  label,
  children,
}: {
  resource: Resource<T>;
  label: string;
  children: (data: T) => ReactNode;
}) {
  if (resource.data !== null) return <>{children(resource.data)}</>;
  if (resource.error !== null) return <PanelError message={resource.error} onRetry={resource.retry} />;
  return <PanelSkeleton label={label} />;
}
