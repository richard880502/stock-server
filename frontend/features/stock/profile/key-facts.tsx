"use client";

import { useScreenerMeta } from "@/features/screener/use-screener-meta";
import type { StockFactors } from "@/lib/api/profile";
import { emptyReason, rankText, scoreBars } from "./model";

export function KeyFactsSkeleton() {
  return <span className="skeleton block" aria-hidden="true" style={{ height: 44 }} />;
}

/** One row above the tabs: screener highlights, the rank and the 0-100 scores. */
export function KeyFacts({ factors }: { factors: StockFactors }) {
  const { meta } = useScreenerMeta();
  const entry = factors.entry;
  if (!entry) {
    return (
      <p className="pf-keyfacts-empty" role="status">
        {emptyReason("factors", factors.notice)}
      </p>
    );
  }
  const rank = rankText(entry.rank, factors.universe);
  const bars = scoreBars(entry, meta);
  return (
    <div className="pf-keyfacts" aria-label="重點事實">
      <ul className="pf-chips">
        {rank && <li className="pf-fact rank">{rank}</li>}
        {entry.highlights.map((text) => (
          <li key={text} className="pf-fact">
            {text}
          </li>
        ))}
        {entry.flags.map((flag) => (
          <li key={flag} className="pf-fact warn">
            {flag === "disposition" ? "處置股" : flag === "attention" ? "注意股" : flag}
          </li>
        ))}
      </ul>
      {bars.length > 0 && (
        <ul className="pf-scores" aria-label="因子分數（0-100）">
          {bars.map((bar) => (
            <li key={bar.key}>
              <span className="pf-score-label">{bar.label}</span>
              <span
                className="pf-bar"
                role="meter"
                aria-label={bar.label}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(bar.value)}
              >
                <span className="pf-bar-fill" style={{ width: `${Math.min(100, Math.max(0, bar.value))}%` }} />
              </span>
              <span className="pf-score-value num">{Math.round(bar.value)}</span>
            </li>
          ))}
        </ul>
      )}
      {factors.data_through && <p className="pf-date num">選股快照資料日 {factors.data_through}</p>}
    </div>
  );
}
