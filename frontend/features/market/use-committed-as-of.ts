"use client";

import { useEffect, useState } from "react";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The shared as-of date is bound to a date input, so it changes while the user
 * is still typing. Only forward complete dates, after a short pause.
 */
export function useCommittedAsOf(asOf: string, delayMs = 350): string {
  const [committed, setCommitted] = useState(asOf);
  useEffect(() => {
    if (!ISO_DATE.test(asOf) || asOf === committed) return;
    const timer = window.setTimeout(() => setCommitted(asOf), delayMs);
    return () => window.clearTimeout(timer);
  }, [asOf, committed, delayMs]);
  return committed;
}
