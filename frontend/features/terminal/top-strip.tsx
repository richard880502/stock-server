"use client";

import type { LiveState } from "./types";

/** Placeholder: market strip (index, live status, clock). Filled in by the rail work. */
export function TopStrip({ live }: { live: LiveState }) {
  return <div className="terminal-strip" data-live={live.status} />;
}
