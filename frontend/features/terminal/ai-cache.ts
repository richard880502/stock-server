import type { DebateReport } from "@/lib/api/types";

export type AiMessage = { question: string; answer: string };

/** A finished streamed operation guide. `price` is the live quote when it finished. */
export type AiGuide = {
  text: string;
  model: string | null;
  generatedAt: number;
  price: number | null;
};

export type AiDebate = {
  report: DebateReport;
  generatedAt: number;
  price: number | null;
};

export type AiCacheEntry = {
  guide?: AiGuide;
  debate?: AiDebate;
  /** Follow-up questions about `debate`; cleared whenever the debate is replaced. */
  messages: AiMessage[];
};

const EMPTY_MESSAGES: AiMessage[] = [];

export const aiCacheKey = (symbol: string, asOf: string) =>
  `${symbol.trim().toUpperCase()}:${asOf}`;

/**
 * Per-symbol, per-day store for finished AI results. The dashboard drops its LLM
 * state whenever the symbol changes, and Claude calls cost money, so switching away
 * and back should not force a second paid run. Entries are immutable snapshots.
 */
export function createAiCache(maxEntries = 60) {
  const entries = new Map<string, AiCacheEntry>();
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((listener) => listener());

  const put = (key: string, entry: AiCacheEntry) => {
    // Re-insert so the Map's order doubles as recency; evict the oldest when full.
    entries.delete(key);
    entries.set(key, entry);
    while (entries.size > maxEntries) {
      const oldest = entries.keys().next().value;
      if (oldest === undefined) break;
      entries.delete(oldest);
    }
    notify();
  };
  const current = (key: string): AiCacheEntry => entries.get(key) ?? { messages: EMPTY_MESSAGES };

  return {
    get: (key: string) => entries.get(key),
    saveGuide(key: string, guide: AiGuide) {
      put(key, { ...current(key), guide });
    },
    saveDebate(key: string, debate: AiDebate) {
      put(key, { ...current(key), debate, messages: EMPTY_MESSAGES });
    },
    addMessage(key: string, message: AiMessage) {
      const entry = current(key);
      put(key, { ...entry, messages: [...entry.messages, message] });
    },
    /** For useSyncExternalStore: `get(key)` is a stable snapshot between writes. */
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    clear() {
      entries.clear();
      notify();
    },
    get size() {
      return entries.size;
    },
  };
}

export type AiCache = ReturnType<typeof createAiCache>;

/** Shared across the page's lifetime (module scope survives route changes). */
export const aiCache: AiCache = createAiCache();

const pad = (value: number) => String(value).padStart(2, "0");
const clock = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** "今天稍早產生 · 14:32" for today's results, "11/25 14:32 產生" otherwise. */
export function generatedLabel(generatedAt: number, now: number = Date.now()): string {
  const at = new Date(generatedAt);
  if (sameDay(at, new Date(now))) return `今天稍早產生 · ${clock(at)}`;
  return `${pad(at.getMonth() + 1)}/${pad(at.getDate())} ${clock(at)} 產生`;
}
