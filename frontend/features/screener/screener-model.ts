import type {
  MarketScope,
  ScreenerEntry,
  ScreenerMeta,
  ScreenerMetric,
  ScreenerOrder,
  ScreenerPreset,
} from "@/lib/api/types";
import { directionOf, formatSigned, formatTwdCompact } from "@/lib/format";

/*
 * Pure model of the 選股 page: URL <-> state, request query, value formatting, sort toggling.
 * Everything that depends on the server catalogue (labels, units, presets) takes the `meta`
 * the page fetched from GET /market/screener/meta; nothing here lists factors or metrics.
 */

export const DEFAULT_PRESET = "balanced";
export const PAGE_SIZE = 50;
export const SCREENER_MARKETS: { value: MarketScope; label: string }[] = [
  { value: "ALL", label: "全部" },
  { value: "TW", label: "上市" },
  { value: "TWO", label: "上櫃" },
];
export const TURNOVER_KEY = "avg_turnover_20d";
/** Options of the 日均成交值下限 select (NT$). */
export const TURNOVER_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: "不限" },
  { value: 1e7, label: "1,000 萬" },
  { value: 5e7, label: "5,000 萬" },
  { value: 1e8, label: "1 億" },
  { value: 5e8, label: "5 億" },
];

export type RangeFilter = { min: number | null; max: number | null };

export type ScreenerState = {
  preset: string;
  market: MarketScope;
  /** null = the preset's own sort / order. */
  sort: string | null;
  order: ScreenerOrder | null;
  /** The user touched the filters: `filters` / `flags` are explicit and the preset defaults no longer apply. */
  custom: boolean;
  filters: Record<string, RangeFilter>;
  flags: string[];
  industries: string[];
  /** false (default) = exclude 處置股. */
  includeRisk: boolean;
  q: string;
};

export const INITIAL_STATE: ScreenerState = {
  preset: DEFAULT_PRESET,
  market: "ALL",
  sort: null,
  order: null,
  custom: false,
  filters: {},
  flags: [],
  industries: [],
  includeRisk: false,
  q: "",
};

/** What is actually applied: the preset's defaults unless the user customised them. */
export type Effective = {
  preset: ScreenerPreset | null;
  sort: string;
  order: ScreenerOrder;
  filters: Record<string, RangeFilter>;
  flags: string[];
};

/* ---- Metric classification ------------------------------------------------ */

const BOOL_UNITS = new Set(["bool", "boolean", "flag", "是/否"]);

export const isTextMetric = (metric: Pick<ScreenerMetric, "kind"> | undefined): boolean =>
  metric !== undefined && metric.kind === "text";

/** Only these get range inputs and can be sorted on. */
export const isNumberMetric = (metric: Pick<ScreenerMetric, "kind" | "unit"> | undefined): boolean =>
  metric !== undefined && !isBoolMetric(metric) && !isTextMetric(metric);

export const isBoolMetric = (metric: Pick<ScreenerMetric, "kind" | "unit"> | undefined): boolean =>
  metric !== undefined && (metric.kind === "bool" || BOOL_UNITS.has(metric.unit));

/** Metrics whose sign carries meaning (net buys, returns, changes): coloured red/green. */
const SIGNED_KEY = /(^|_)(net|ret|change|streak|cmf|yoy|mom)(_|$)|flow_ratio|_gap_pct$/;
export const isSignedMetric = (metric: { key: string; unit?: string; signed?: boolean }): boolean => {
  if (metric.signed !== undefined) return metric.signed;
  if (metric.key === "high_52w_gap_pct") return false; // always <= 0, would read as permanently "down"
  return SIGNED_KEY.test(metric.key);
};

/* ---- Filter <-> text ------------------------------------------------------ */

const KEY_PATTERN = /^[a-z][a-z0-9_]*$/;

const parseBound = (text: string): number | null => {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
};

/** `key:min:max` (either bound may be empty) as sent in `f=` and kept in the page URL. */
export function parseFilterParam(text: string): ({ key: string } & RangeFilter) | null {
  const parts = text.split(":");
  if (parts.length !== 3 || !KEY_PATTERN.test(parts[0])) return null;
  const min = parseBound(parts[1]);
  const max = parseBound(parts[2]);
  if (min === null && max === null) return null;
  return { key: parts[0], min, max };
}

export const formatFilterParam = (key: string, range: RangeFilter): string =>
  `${key}:${range.min ?? ""}:${range.max ?? ""}`;

const isActive = (range: RangeFilter | undefined): range is RangeFilter =>
  range !== undefined && (range.min !== null || range.max !== null);

/* ---- Presets -------------------------------------------------------------- */

export function findPreset(meta: ScreenerMeta | null, key: string): ScreenerPreset | null {
  if (!meta || meta.presets.length === 0) return null;
  return (
    meta.presets.find((preset) => preset.key === key) ??
    meta.presets.find((preset) => preset.key === DEFAULT_PRESET) ??
    meta.presets[0]
  );
}

/**
 * Why an empty preset is empty because data is still accumulating: the first metric the preset
 * needs that no stock has a value for yet, as a user-facing sentence; null otherwise.
 */
export function accumulatingNotice(
  preset: ScreenerPreset | null,
  unavailable: string[] | null | undefined,
  meta: ScreenerMeta | null,
): string | null {
  if (!preset?.requires?.length || !unavailable?.length) return null;
  const missing = preset.requires.find((key) => unavailable.includes(key));
  if (!missing) return null;
  const label = meta?.metrics.find((metric) => metric.key === missing)?.label ?? missing;
  return `這個策略需要的「${label}」資料還在累積中，補齊後才會有結果`;
}

export function presetDefaults(
  preset: ScreenerPreset | null,
  meta: ScreenerMeta | null,
): { filters: Record<string, RangeFilter>; flags: string[] } {
  const filters: Record<string, RangeFilter> = {};
  const flags: string[] = [];
  for (const item of preset?.filters ?? []) {
    const metric = meta?.metrics.find((m) => m.key === item.key);
    if (item.flag === true || item.value === true || item.equals === true || isBoolMetric(metric)) {
      if (!flags.includes(item.key)) flags.push(item.key);
      continue;
    }
    const range = { min: item.min ?? null, max: item.max ?? null };
    if (isActive(range)) filters[item.key] = range;
  }
  return { filters, flags };
}

export function resolveEffective(state: ScreenerState, meta: ScreenerMeta | null): Effective {
  const preset = findPreset(meta, state.preset);
  const defaults = presetDefaults(preset, meta);
  return {
    preset,
    sort: state.sort ?? preset?.sort ?? "composite",
    order: state.order ?? preset?.order ?? "desc",
    filters: state.custom ? state.filters : defaults.filters,
    flags: state.custom ? state.flags : defaults.flags,
  };
}

/* ---- URL ------------------------------------------------------------------ */

type SearchSource = Pick<URLSearchParams, "get" | "getAll">;

/** Unknown or malformed values fall back to the defaults, like the market page. */
export function parseScreenerParams(search: SearchSource): ScreenerState {
  const market = search.get("market");
  const order = search.get("order");
  const sort = search.get("sort");
  const filters: Record<string, RangeFilter> = {};
  for (const text of search.getAll("f")) {
    const parsed = parseFilterParam(text);
    if (parsed) filters[parsed.key] = { min: parsed.min, max: parsed.max };
  }
  const flags = [...new Set(search.getAll("flag").filter((key) => KEY_PATTERN.test(key)))];
  const industries = [...new Set(search.getAll("ind").filter((code) => /^[\w-]{1,12}$/.test(code)))];
  return {
    preset: search.get("preset") && KEY_PATTERN.test(search.get("preset")!) ? search.get("preset")! : DEFAULT_PRESET,
    market: market === "TW" || market === "TWO" ? market : "ALL",
    sort: sort && KEY_PATTERN.test(sort) ? sort : null,
    order: order === "asc" || order === "desc" ? order : null,
    custom: search.get("custom") === "1" || Object.keys(filters).length > 0 || flags.length > 0,
    filters,
    flags,
    industries,
    includeRisk: search.get("risk") === "1",
    q: (search.get("q") ?? "").slice(0, 40),
  };
}

export function serializeScreenerParams(state: ScreenerState): URLSearchParams {
  const query = new URLSearchParams();
  if (state.preset !== DEFAULT_PRESET) query.set("preset", state.preset);
  if (state.market !== "ALL") query.set("market", state.market);
  if (state.sort) query.set("sort", state.sort);
  if (state.order) query.set("order", state.order);
  if (state.custom) {
    query.set("custom", "1");
    for (const [key, range] of Object.entries(state.filters)) {
      if (isActive(range)) query.append("f", formatFilterParam(key, range));
    }
    for (const key of state.flags) query.append("flag", key);
  }
  for (const code of state.industries) query.append("ind", code);
  if (state.includeRisk) query.set("risk", "1");
  if (state.q.trim()) query.set("q", state.q.trim());
  return query;
}

/* ---- Request -------------------------------------------------------------- */

/**
 * The query string of GET /market/screener (without `limit` / `offset`, so it doubles as the
 * cache key of one result list). Filters, sort and order are always sent explicitly, so removing
 * a preset's own filter cannot be undone by the server re-applying the preset; `preset` is only
 * added while the screen is still the untouched preset (it makes the server echo it back).
 */
export function buildScreenerQuery(
  state: ScreenerState,
  effective: Effective,
  options: { asOf?: string | null } = {},
): URLSearchParams {
  const query = new URLSearchParams();
  query.set("market", state.market);
  if (options.asOf) query.set("as_of", options.asOf);
  const untouched = !state.custom && state.sort === null && state.order === null;
  if (untouched && effective.preset) query.set("preset", effective.preset.key);
  query.set("sort", effective.sort);
  query.set("order", effective.order);
  for (const [key, range] of Object.entries(effective.filters)) {
    if (isActive(range)) query.append("f", formatFilterParam(key, range));
  }
  for (const key of effective.flags) query.append("flag", key);
  for (const code of state.industries) query.append("industry", code);
  query.set("exclude_risk", state.includeRisk ? "false" : "true");
  if (state.q.trim()) query.set("q", state.q.trim());
  return query;
}

export function withPage(base: URLSearchParams, limit: number, offset: number): URLSearchParams {
  const query = new URLSearchParams(base);
  query.set("limit", String(limit));
  query.set("offset", String(offset));
  return query;
}

/* ---- State transitions ---------------------------------------------------- */

/** Picking a preset resets sort and filters to its defaults; market, industries, q and the 處置 toggle stay. */
export const selectPreset = (state: ScreenerState, key: string): ScreenerState => ({
  ...state,
  preset: key,
  sort: null,
  order: null,
  custom: false,
  filters: {},
  flags: [],
});

const materialize = (state: ScreenerState, effective: Effective): ScreenerState =>
  state.custom ? state : { ...state, custom: true, filters: { ...effective.filters }, flags: [...effective.flags] };

export function setRange(
  state: ScreenerState,
  effective: Effective,
  key: string,
  patch: Partial<RangeFilter>,
): ScreenerState {
  const base = materialize(state, effective);
  const next = { ...(base.filters[key] ?? { min: null, max: null }), ...patch };
  const filters = { ...base.filters };
  if (isActive(next)) filters[key] = next;
  else delete filters[key];
  return { ...base, filters };
}

export function setFlag(state: ScreenerState, effective: Effective, key: string, on: boolean): ScreenerState {
  const base = materialize(state, effective);
  const flags = base.flags.filter((k) => k !== key);
  if (on) flags.push(key);
  return { ...base, flags };
}

export const clearFilters = (state: ScreenerState, effective: Effective): ScreenerState => ({
  ...materialize(state, effective),
  filters: {},
  flags: [],
  industries: [],
  q: "",
});

/** Click on a sortable header: a new column starts at its better-first direction, the same one flips. */
export function toggleSort(
  current: { sort: string; order: ScreenerOrder },
  key: string,
  higherIsBetter: boolean | null = true,
): { sort: string; order: ScreenerOrder } {
  if (current.sort === key) return { sort: key, order: current.order === "desc" ? "asc" : "desc" };
  return { sort: key, order: higherIsBetter === false ? "asc" : "desc" };
}

export function applySort(state: ScreenerState, sort: string, order: ScreenerOrder): ScreenerState {
  return { ...state, sort, order };
}

/* ---- Values --------------------------------------------------------------- */

export type ValueSpec = { key?: string; unit: string; decimals?: number | null; signed?: boolean };

const group = (value: number, digits: number) =>
  value.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** A metric value by its meta unit; missing values are an em dash, never 0. */
export function formatScreenerValue(
  value: number | boolean | string | null | undefined,
  spec: ValueSpec,
): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value || "—";
  if (!Number.isFinite(value)) return "—";
  const signed = spec.signed ?? isSignedMetric({ key: spec.key ?? "", unit: spec.unit });
  const num = (digits: number) => (signed ? formatSigned(value, digits) : group(value, digits));
  switch (spec.unit) {
    case "元":
      return formatTwdCompact(value);
    case "%":
      return `${num(spec.decimals ?? 1)}%`;
    case "張":
      return `${num(0)} 張`;
    case "日":
    case "天":
      return `${num(0)} ${spec.unit}`;
    case "分":
      return num(spec.decimals ?? 1);
    case "":
      return num(spec.decimals ?? (Number.isInteger(value) ? 0 : 2));
    default:
      return `${num(spec.decimals ?? (Number.isInteger(value) ? 0 : 2))} ${spec.unit}`;
  }
}

export const formatScore = (value: number | null | undefined): string =>
  value === null || value === undefined ? "—" : String(Math.round(value));

export const toneOf = (value: unknown, signed: boolean): "up" | "down" | "flat" =>
  signed && typeof value === "number" ? directionOf(value) : "flat";

/** Bound of an active range as readable text: "≥ 2,000 萬", "−8 ~ 8%". */
export function describeRange(range: RangeFilter, spec: ValueSpec): string {
  const plain = { ...spec, signed: false };
  // The backend sends "> 0" presets as a tiny positive minimum (0.0001).
  const fmt = (n: number) => formatScreenerValue(n, plain);
  if (range.min !== null && range.min > 0 && range.min <= 0.001 && range.max === null) return "> 0";
  if (range.min !== null && range.max !== null) return `${fmt(range.min)} ~ ${fmt(range.max)}`;
  if (range.min !== null) return `≥ ${fmt(range.min)}`;
  return `≤ ${fmt(range.max ?? 0)}`;
}

export const FLAG_LABELS: Record<string, string> = {
  disposition: "處置",
  attention: "注意",
  處置: "處置",
  注意: "注意",
};
export const flagLabel = (flag: string): string => FLAG_LABELS[flag] ?? flag;

/** First `visible` highlights plus how many are folded into the "+n" chip. */
/** The 資料截止 / 籌碼資料至 pair for the results header: the flow date only shows when it differs. */
export function resultDates(data: { data_through: string | null; flow_through: string | null }) {
  const flowThrough = data.flow_through && data.flow_through !== data.data_through ? data.flow_through : null;
  return { dataThrough: data.data_through, flowThrough, lagging: Boolean(flowThrough && data.data_through && flowThrough < data.data_through) };
}

/** "Calculating" state: the server has no snapshot yet and gave no notice that explains why. */
export const isPending = (data: { snapshot_pending: boolean; notice?: string | null } | null) =>
  data?.snapshot_pending === true && !data.notice;

export function splitHighlights(highlights: string[], visible = 3) {
  return { shown: highlights.slice(0, visible), hidden: Math.max(0, highlights.length - visible) };
}

/* ---- Columns -------------------------------------------------------------- */

export type ColumnDef = {
  key: string;
  label: string;
  /** Compact header / card title; equals `label` when the server sends no `short`. */
  short: string;
  kind: "score" | "metric";
  unit: string;
  decimals: number | null;
  signed: boolean;
  higherIsBetter: boolean | null;
  description: string;
  sortable: boolean;
};

export function resolveColumn(meta: ScreenerMeta | null, key: string): ColumnDef {
  const factor = meta?.factors.find((f) => f.key === key);
  if (factor || key === "composite") {
    return {
      key,
      label: factor?.label ?? "綜合",
      short: factor?.short ?? factor?.label ?? "綜合",
      kind: "score",
      unit: factor?.unit ?? "分",
      decimals: 0,
      signed: false,
      higherIsBetter: factor?.higher_is_better ?? true,
      description: factor?.description ?? "各因子分數依權重合成的綜合分數（0–100）。",
      sortable: true,
    };
  }
  const metric = meta?.metrics.find((m) => m.key === key);
  return {
    key,
    label: metric?.label ?? key,
    short: metric?.short ?? metric?.label ?? key,
    kind: "metric",
    unit: metric?.unit ?? "",
    decimals: metric?.decimals ?? null,
    signed: !isTextMetric(metric) && isSignedMetric({ key, unit: metric?.unit, signed: metric?.signed }),
    higherIsBetter: metric?.higher_is_better ?? null,
    description: metric?.description ?? "",
    sortable: metric === undefined || isNumberMetric(metric),
  };
}

export function cellValue(entry: ScreenerEntry, key: string): number | boolean | string | null {
  if (key === "composite") return entry.composite;
  if (key in entry.scores) return entry.scores[key];
  if (key in entry.metrics) return entry.metrics[key] ?? null;
  if (key === "change_pct") return entry.change_pct;
  if (key === TURNOVER_KEY) return entry.avg_turnover_20d;
  return null;
}

/** The columns of a table / card: composite is always shown separately, so it is dropped here. */
export function visibleColumns(effective: Effective): string[] {
  const keys = (effective.preset?.columns ?? []).filter((key) => key !== "composite");
  if (effective.sort !== "composite" && !keys.includes(effective.sort)) keys.push(effective.sort);
  return keys;
}

export const codeOf = (symbol: string) => symbol.replace(/\.TWO?$/, "");
