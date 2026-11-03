import { readJson, writeStorage } from "./safe-storage";

const KEY = "quant_signal_recent_symbols";
const MAX_RECENT = 6;

export type RecentSymbol = { symbol: string; name?: string };

export function getRecentSymbols(): RecentSymbol[] {
  const raw = readJson<unknown>(KEY, []);
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(
      (item): item is RecentSymbol =>
        !!item && typeof item === "object" && typeof (item as RecentSymbol).symbol === "string",
    )
    .slice(0, MAX_RECENT);
}

/** Put `item` first, de-duplicated; returns the new list. */
export function rememberSymbol(item: RecentSymbol): RecentSymbol[] {
  const previous = getRecentSymbols();
  const known = previous.find((entry) => entry.symbol === item.symbol);
  const next = [
    { symbol: item.symbol, name: item.name ?? known?.name },
    ...previous.filter((entry) => entry.symbol !== item.symbol),
  ].slice(0, MAX_RECENT);
  writeStorage(KEY, JSON.stringify(next));
  return next;
}

export function clearRecentSymbols(): void {
  writeStorage(KEY, null);
}
