import { isStaleQuote, referencePrice } from "@/features/terminal/rail-stale";
import type { StockOverview } from "@/lib/api/types";
import type { LiveQuote, LiveQuoteStatus } from "@/lib/live-quotes";
import type { StockSection } from "@/lib/stock-routes";

/** The sections of the stock page in reading order; ids are the anchors in `stockHref(symbol, section)`. */
export const SECTION_LINKS: { id: StockSection; label: string }[] = [
  { id: "summary", label: "結論" },
  { id: "chart", label: "走勢" },
  { id: "evidence", label: "證據" },
  { id: "profile", label: "全貌" },
  { id: "chips", label: "籌碼" },
];

const SECTION_IDS = new Set<string>(SECTION_LINKS.map((section) => section.id));

/**
 * The section a URL hash points at: `#chart` → chart, `#profile=earnings` → profile
 * (the 個股全貌 tabs keep their own `key=value` form). Null for anything else.
 */
export function sectionFromHash(hash: string | null | undefined): StockSection | null {
  const name = (hash ?? "").trim().replace(/^#/, "").split("=")[0];
  return SECTION_IDS.has(name) ? (name as StockSection) : null;
}

/**
 * The section the reader is in: the last one whose top has reached `line` (pixels from the
 * viewport top). `tops` holds each present section's top, null for one not on the page.
 * Before any section reaches the line the first present one counts; at the very bottom of
 * the page the last present one does, since a short last section may never reach the line.
 */
export function pickActiveSection(
  tops: Partial<Record<StockSection, number | null>>,
  line: number,
  atBottom = false,
): StockSection | null {
  const present = SECTION_LINKS.filter(({ id }) => typeof tops[id] === "number");
  if (present.length === 0) return null;
  if (atBottom) return present[present.length - 1].id;
  let active = present[0].id;
  for (const { id } of present) if ((tops[id] as number) <= line) active = id;
  return active;
}

export type HeaderPrice = {
  price: number;
  /** Ratio (-0.0359 = -3.59%); null when unknown. */
  changePct: number | null;
  /** Absolute change in price units; null when it cannot be derived. */
  change: number | null;
  source: "live" | "daily";
  /** Live quotes only: the stream is down or the last tick is old. */
  stale: boolean;
  /** Live: when the last trade happened; daily: the trading date of the close. */
  asOfLabel: string;
  quote: LiveQuote | null;
};

function changeFrom(price: number, changePct: number | null): number | null {
  const reference = changePct === null ? null : referencePrice(price, changePct);
  return reference === null ? null : price - reference;
}

/**
 * The price the stock header shows: the live quote when the stream has delivered one,
 * otherwise the latest daily close from the quick overview. Null when neither exists
 * (the header then shows a placeholder, never a zero).
 */
export function pickHeaderPrice(input: {
  quote: LiveQuote | undefined;
  status: LiveQuoteStatus;
  now: number | null;
  overview: Pick<StockOverview, "symbol" | "data_through" | "summary"> | null;
  symbol: string;
}): HeaderPrice | null {
  const { quote, status, now, overview, symbol } = input;
  if (quote && Number.isFinite(quote.price) && quote.price > 0) {
    return {
      price: quote.price,
      changePct: quote.change_pct,
      change: changeFrom(quote.price, quote.change_pct),
      source: "live",
      stale: now !== null && isStaleQuote(quote, status, now),
      asOfLabel: quote.trade_time,
      quote,
    };
  }
  const summary = overview && overview.symbol.toUpperCase() === symbol.toUpperCase() ? overview.summary : null;
  if (summary && typeof summary.last_close === "number" && summary.last_close > 0) {
    // The overview API sends percent (-3.59); live quotes send a ratio.
    const changePct = summary.day_change_pct == null ? null : summary.day_change_pct / 100;
    return {
      price: summary.last_close,
      changePct,
      change: changeFrom(summary.last_close, changePct),
      source: "daily",
      stale: false,
      asOfLabel: overview?.data_through ?? "",
      quote: null,
    };
  }
  return null;
}

/** `2330.TW` → `2330`. */
export const bareCode = (symbol: string) => symbol.replace(/\.TWO?$/i, "");

/** `2330 台積電 — Quant Signal`, or without the name until it is known. */
export const stockTitle = (symbol: string, name: string | null | undefined) =>
  `${[bareCode(symbol), name].filter(Boolean).join(" ")} — Quant Signal`;
