import { sectorReturn } from "@/features/market/sector-math";
import { positionWord } from "@/features/market/derivatives-model";
import {
  cellValue,
  DEFAULT_PRESET,
  formatScreenerValue,
  resolveColumn,
  toneOf as screenerToneOf,
} from "@/features/screener/screener-model";
import type { AccountSummary } from "@/lib/api/paper";
import type { MarketDerivatives, MarketDerivativesDay } from "@/lib/api/profile";
import type {
  Alert,
  MarketEnvironment,
  ScreenerEntry,
  ScreenerMeta,
  SectorRow,
  WatchlistItem,
} from "@/lib/api/types";
import { directionOf, formatNumber, formatSigned } from "@/lib/format";

export type Tone = "up" | "down" | "flat";

/* ---- Watchlist -------------------------------------------------------------------------- */

export const WATCHLIST_ROWS = 8;

export type WatchRow = {
  symbol: string;
  code: string;
  name: string;
  price: number | null;
  /** Percent, e.g. -3.59. */
  changePct: number | null;
  closes: number[];
};

/** The first `limit` watchlist items as display rows; today's latest price wins over the close. */
export function watchRows(items: WatchlistItem[] | null | undefined, limit = WATCHLIST_ROWS): WatchRow[] {
  return (items ?? []).slice(0, limit).map((item) => {
    const fresh = item.latest_price != null;
    return {
      symbol: item.symbol,
      code: item.symbol.replace(/\.TWO?$/, ""),
      name: item.name,
      price: fresh ? (item.latest_price ?? null) : (item.close ?? null),
      changePct: fresh ? (item.latest_change_pct ?? item.day_change_pct ?? null) : (item.day_change_pct ?? null),
      closes: item.recent_closes ?? [],
    };
  });
}

/* ---- Market environment ----------------------------------------------------------------- */

const SEVERITY_RANK: Record<Alert["severity"], number> = { high: 0, medium: 1, info: 2 };

/** The most severe alerts first (stable within a severity), at most `limit`. */
export function topAlerts(environment: Pick<MarketEnvironment, "alerts"> | null, limit = 2): Alert[] {
  const alerts = environment?.alerts;
  if (!alerts?.length) return [];
  return alerts
    .map((alert, index) => ({ alert, index }))
    .sort((a, b) => SEVERITY_RANK[a.alert.severity] - SEVERITY_RANK[b.alert.severity] || a.index - b.index)
    .slice(0, limit)
    .map((item) => item.alert);
}

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** Three label/value facts of a market-environment card; a missing field is "—", never 0 or NaN. */
export function environmentFacts(environment: Pick<MarketEnvironment, "latest"> | null | undefined): Headline[] | null {
  const latest = environment?.latest;
  if (!latest) return null;
  const up = finite(latest.advancing_issues);
  const down = finite(latest.declining_issues);
  const total = finite(latest.total_issues);
  const above = finite(latest.above_ma20_issues);
  const foreign = finite(latest.foreign_net_flow);
  const upShare = up != null && down != null && up + down > 0 ? up / (up + down) : null;
  const aboveShare = above != null && total != null && total > 0 ? above / total : null;
  return [
    {
      key: "advdec",
      label: "漲跌家數",
      value: up != null && down != null ? `上漲 ${formatNumber(up, 0)} · 下跌 ${formatNumber(down, 0)}` : "—",
      tone: "flat",
      note: "",
      share: upShare,
    },
    {
      key: "ma20",
      label: "站上月線",
      value: aboveShare != null ? `${formatNumber(aboveShare * 100, 1)}%` : "—",
      tone: "flat",
      note:
        aboveShare != null && above != null && total != null
          ? `${formatNumber(above, 0)} / ${formatNumber(total, 0)} 檔`
          : "尚無資料",
    },
    {
      key: "foreign",
      label: "外資買賣超",
      value: foreign != null ? `${formatSigned(foreign / 1e8, 1)} 億` : "—",
      tone: foreign != null ? directionOf(foreign) : "flat",
      note: foreign != null ? "NT$，當日" : "尚無資料",
    },
  ];
}

/* ---- Derivatives ------------------------------------------------------------------------ */

/** `share` (0..1) draws a two-tone up/down bar under the row. */
export type Headline = {
  key: string;
  label: string;
  value: string;
  tone: Tone;
  note: string;
  share?: number | null;
};

/** Newest day with a real number for `pick`; an API one release behind may omit the field entirely. */
function newest(days: MarketDerivativesDay[], pick: (day: MarketDerivativesDay) => number | null | undefined) {
  for (let i = days.length - 1; i >= 0; i -= 1) {
    const value = pick(days[i]);
    if (typeof value === "number" && Number.isFinite(value)) return { date: days[i].date, value };
  }
  return null;
}

/** Three headline numbers of 期貨與選擇權; a missing value is "—" with its own note, never 0. */
export function derivativeHeadlines(data: MarketDerivatives | null): Headline[] {
  const days = data?.days ?? [];
  const foreign = newest(days, (d) => d.foreign_tx_equiv_net_oi);
  const change = newest(days, (d) => d.foreign_net_oi_change);
  const pc = newest(days, (d) => d.pc_oi_ratio);
  const vix = newest(days, (d) => d.vix);
  const date = (iso: string) => iso.slice(5).replace("-", "/");
  return [
    {
      key: "foreign",
      label: "外資台指期淨未平倉（口）",
      value: foreign ? formatSigned(foreign.value, 0) : "—",
      tone: foreign ? directionOf(foreign.value) : "flat",
      note: foreign
        ? `${positionWord(foreign.value)}${change ? `，較前日 ${formatSigned(change.value, 0)}` : ""}・${date(foreign.date)}`
        : "尚無資料",
    },
    {
      key: "pc",
      label: "Put/Call 未平倉比",
      value: pc ? `${formatNumber(pc.value, 1)}%` : "—",
      tone: "flat",
      note: pc ? date(pc.date) : "尚無資料",
    },
    {
      key: "vix",
      label: "VIX 波動率指數",
      value: vix ? formatNumber(vix.value, 2) : "—",
      tone: "flat",
      note: vix ? date(vix.date) : "尚無資料",
    },
  ];
}

/* ---- Screener candidates ---------------------------------------------------------------- */

/** The presets the home page offers, in order. */
export const CANDIDATE_PRESETS = ["quiet_accumulation", "balanced", "momentum"] as const;
export const CANDIDATE_ROWS = 5;
export const CANDIDATE_DISCLAIMER = "依條件篩出的排行，並非買進建議。";

/** The candidate presets the server's catalogue really has; unknown keys are dropped silently. */
export function candidatePresets(meta: ScreenerMeta | null): { key: string; label: string; description: string }[] {
  if (!meta) return [];
  return CANDIDATE_PRESETS.flatMap((key) => {
    const preset = meta.presets.find((p) => p.key === key);
    return preset ? [{ key, label: preset.label, description: preset.description }] : [];
  });
}

/** 選股 page with a preset chosen (the default preset has no parameter). */
export const screenerHref = (preset: string): string =>
  preset === DEFAULT_PRESET ? "/screener" : `/screener?preset=${encodeURIComponent(preset)}`;

export type Support = { label: string; text: string; tone: Tone };

/**
 * One metric that explains why a stock is in the preset's list: the first of the preset's own
 * columns (after the sort column and the composite) that has a real number, preferring raw
 * metrics (20 日報酬, 法人買超比) over factor scores; null when there is none.
 */
export function supportingMetric(
  raw: ScreenerEntry,
  presetKey: string,
  meta: ScreenerMeta | null,
): Support | null {
  const entry: ScreenerEntry = { ...raw, scores: raw.scores ?? {}, metrics: raw.metrics ?? {} };
  const preset = meta?.presets.find((p) => p.key === presetKey);
  const keys = (preset?.columns ?? []).filter((key) => key !== "composite" && key !== preset?.sort);
  const pick = (wantMetric: boolean) =>
    keys.find((key) => {
      const column = resolveColumn(meta, key);
      const value = cellValue(entry, key);
      return (column.kind === "metric") === wantMetric && typeof value === "number" && Number.isFinite(value);
    });
  const key = pick(true) ?? pick(false);
  if (!key) return null;
  const column = resolveColumn(meta, key);
  const value = cellValue(entry, key);
  return {
    label: column.short,
    text: formatScreenerValue(value, column),
    tone: screenerToneOf(value, column.signed),
  };
}

/* ---- Sectors ---------------------------------------------------------------------------- */

export type SectorPick = { code: string; name: string; ret: number };

/**
 * The strongest and weakest sectors by the equal-weight return over `window` sessions (the
 * market page's headline column). Sectors without a return are skipped; with fewer than
 * 2×`count` sectors the two lists never overlap.
 */
export function extremeSectors(
  rows: SectorRow[] | null | undefined,
  window: number,
  count = 3,
): { strongest: SectorPick[]; weakest: SectorPick[] } {
  const ranked = (rows ?? []).flatMap((row) => {
    const ret = sectorReturn(row, window);
    return ret === null || ret === undefined ? [] : [{ code: row.code, name: row.name, ret }];
  });
  ranked.sort((a, b) => b.ret - a.ret || a.name.localeCompare(b.name, "zh-TW"));
  const strongestCount = Math.min(count, Math.ceil(ranked.length / 2));
  const strongest = ranked.slice(0, strongestCount);
  const weakest = ranked.slice(strongestCount).slice(-count).reverse();
  return { strongest, weakest };
}

/* ---- Paper account ---------------------------------------------------------------------- */

export type PaperSummary = {
  id: string;
  name: string;
  accountCount: number;
  equity: number | null;
  dayPnl: number | null;
  positions: number | null;
  workingOrders: number | null;
};

const num = (value: number | null | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** The first account of the list as the summary the home card shows; null when there is none. */
export function paperSummary(accounts: AccountSummary[] | null | undefined): PaperSummary | null {
  const first = accounts?.[0];
  if (!first) return null;
  return {
    id: first.id,
    name: first.name,
    accountCount: accounts?.length ?? 1,
    equity: num(first.equity),
    dayPnl: num(first.day_pnl),
    positions: num(first.positions_count),
    workingOrders: num(first.working_orders),
  };
}
