"use client";

import type { LiveQuote } from "@/lib/live-quotes";

/** Placeholder: deterministic signal + on-demand AI suggestions, no order entry. */
export function AiRail({ symbol }: { symbol: string; name: string | null; quote?: LiveQuote }) {
  return <aside className="terminal-ai" aria-label="AI 交易建議">{symbol}</aside>;
}
