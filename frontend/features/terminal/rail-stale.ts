import type { LiveQuote } from "@/lib/live-quotes";
import type { LiveState } from "./types";

export const STALE_AFTER_MS = 30_000;

/** A quote is stale when the stream is not live or it was received more than 30 s ago. */
export function isStaleQuote(quote: LiveQuote, status: LiveState["status"], now: number): boolean {
  if (status !== "live") return true;
  const received = Date.parse(quote.received_at);
  return !Number.isFinite(received) || now - received > STALE_AFTER_MS;
}

/** Previous close implied by the live price and its change ratio; null when undefined. */
export function referencePrice(price: number, changePct: number | null): number | null {
  if (changePct === null || !Number.isFinite(changePct) || changePct <= -1) return null;
  return price / (1 + changePct);
}
