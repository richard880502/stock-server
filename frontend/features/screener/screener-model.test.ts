import { describe, expect, it } from "vitest";
import { META } from "./fixtures";
import {
  buildScreenerQuery,
  clearFilters,
  describeRange,
  formatFilterParam,
  formatScreenerValue,
  INITIAL_STATE,
  parseFilterParam,
  parseScreenerParams,
  resolveColumn,
  resolveEffective,
  selectPreset,
  serializeScreenerParams,
  setFlag,
  setRange,
  splitHighlights,
  toggleSort,
  visibleColumns,
  withPage,
} from "./screener-model";

const parse = (qs: string) => parseScreenerParams(new URLSearchParams(qs));

describe("filter param text", () => {
  it("parses key:min:max with open bounds and negatives", () => {
    expect(parseFilterParam("revenue_yoy_pct:20:")).toEqual({ key: "revenue_yoy_pct", min: 20, max: null });
    expect(parseFilterParam("ret_20d:-8:8")).toEqual({ key: "ret_20d", min: -8, max: 8 });
    expect(parseFilterParam("x_1::5.5")).toEqual({ key: "x_1", min: null, max: 5.5 });
  });
  it("rejects malformed text", () => {
    for (const bad of ["", "ret_20d", "ret_20d:a:b", "ret_20d::", "A B:1:2", "k:1:2:3"]) {
      expect(parseFilterParam(bad)).toBeNull();
    }
  });
  it("round-trips", () => {
    expect(formatFilterParam("ret_20d", { min: -8, max: 8 })).toBe("ret_20d:-8:8");
    expect(formatFilterParam("avg_turnover_20d", { min: 1e8, max: null })).toBe("avg_turnover_20d:100000000:");
  });
});

describe("URL params", () => {
  it("defaults when the URL is empty", () => {
    expect(parse("")).toEqual(INITIAL_STATE);
    expect(serializeScreenerParams(INITIAL_STATE).toString()).toBe("");
  });
  it("round-trips a customised screen", () => {
    const state = {
      ...INITIAL_STATE,
      preset: "money_flow",
      market: "TWO" as const,
      sort: "total_net_20d",
      order: "asc" as const,
      custom: true,
      filters: { ret_20d: { min: -8, max: 8 }, avg_turnover_20d: { min: 1e8, max: null } },
      flags: ["trend_aligned"],
      industries: ["24", "17"],
      includeRisk: true,
      q: "台積",
    };
    const text = serializeScreenerParams(state).toString();
    expect(parse(text)).toEqual(state);
  });
  it("keeps 'all filters cleared' distinct from 'preset defaults'", () => {
    const cleared = { ...INITIAL_STATE, custom: true };
    expect(serializeScreenerParams(cleared).toString()).toBe("custom=1");
    expect(parse("custom=1").custom).toBe(true);
    expect(parse("").custom).toBe(false);
  });
  it("ignores unknown or hostile values", () => {
    const state = parse("market=XX&order=sideways&sort=A%20B&f=bad&flag=%3Cb%3E&ind=%3Cx%3E&preset=Bad-Key");
    expect(state).toEqual(INITIAL_STATE);
  });
});

describe("request query", () => {
  const build = (state = INITIAL_STATE, asOf: string | null = "2026-11-27") =>
    buildScreenerQuery(state, resolveEffective(state, META), { asOf });

  it("sends the untouched preset with its filters explicit", () => {
    const query = build();
    expect(query.toString()).toBe(
      "market=ALL&as_of=2026-11-27&preset=balanced&sort=composite&order=desc&f=avg_turnover_20d%3A20000000%3A&exclude_risk=true",
    );
  });
  it("turns a bool preset filter into flag= and a two-sided range into f=min:max", () => {
    const momentum = build(selectPreset(INITIAL_STATE, "momentum"));
    expect(momentum.getAll("flag")).toEqual(["trend_aligned"]);
    expect(momentum.getAll("f")).toEqual(["avg_turnover_20d:50000000:"]);
    expect(momentum.get("sort")).toBe("momentum");
    const quiet = build(selectPreset(INITIAL_STATE, "quiet_accumulation"));
    expect(quiet.getAll("f")).toContain("ret_20d:-8:8");
    expect(quiet.getAll("f")).toContain("inst_buy_days_60d:55:");
  });
  it("repeats industry, drops preset once customised, and carries risk / q / paging", () => {
    const base = { ...INITIAL_STATE, market: "TW" as const, industries: ["24", "17"], includeRisk: true, q: " 2330 " };
    const custom = { ...base, custom: true };
    const query = withPage(build(custom, null), 50, 100);
    expect(query.getAll("industry")).toEqual(["24", "17"]);
    expect(query.get("preset")).toBeNull();
    expect(query.get("as_of")).toBeNull();
    expect(query.getAll("f")).toEqual([]);
    expect(query.get("exclude_risk")).toBe("false");
    expect(query.get("q")).toBe("2330");
    expect(query.get("limit")).toBe("50");
    expect(query.get("offset")).toBe("100");
  });
  it("removing a preset filter sends no f= for it (and no preset to re-apply it)", () => {
    const eff = resolveEffective(INITIAL_STATE, META);
    const next = setRange(INITIAL_STATE, eff, "avg_turnover_20d", { min: null });
    const query = build(next);
    expect(query.getAll("f")).toEqual([]);
    expect(query.get("preset")).toBeNull();
  });
  it("explicit sort overrides the preset's", () => {
    const state = { ...INITIAL_STATE, sort: "ret_20d", order: "asc" as const };
    const query = build(state);
    expect([query.get("sort"), query.get("order"), query.get("preset")]).toEqual(["ret_20d", "asc", null]);
  });
});

describe("state transitions", () => {
  it("selecting a preset resets sort and filters but keeps market, industries and q", () => {
    const dirty = { ...INITIAL_STATE, market: "TW" as const, industries: ["24"], q: "a", sort: "ret_20d", order: "asc" as const, custom: true, filters: { ret_20d: { min: 1, max: null } }, flags: ["x"] };
    const next = selectPreset(dirty, "money_flow");
    expect(next).toMatchObject({ preset: "money_flow", sort: null, order: null, custom: false, filters: {}, flags: [], market: "TW", industries: ["24"], q: "a" });
  });
  it("first edit copies the preset defaults, later edits keep them", () => {
    const eff = resolveEffective(selectPreset(INITIAL_STATE, "momentum"), META);
    const state = setRange(selectPreset(INITIAL_STATE, "momentum"), eff, "ret_20d", { min: 5 });
    expect(state.custom).toBe(true);
    expect(state.flags).toEqual(["trend_aligned"]);
    expect(Object.keys(state.filters).sort()).toEqual(["avg_turnover_20d", "ret_20d"]);
    expect(setFlag(state, eff, "trend_aligned", false).flags).toEqual([]);
  });
  it("clear-all empties filters, industries and q but stays custom", () => {
    const base = { ...INITIAL_STATE, industries: ["24"], q: "a" };
    const cleared = clearFilters(base, resolveEffective(base, META));
    expect(cleared).toMatchObject({ custom: true, filters: {}, flags: [], industries: [], q: "" });
  });
  it("an emptied range disappears", () => {
    const eff = resolveEffective(INITIAL_STATE, META);
    const state = setRange(INITIAL_STATE, eff, "ret_20d", { min: 5 });
    expect(setRange(state, eff, "ret_20d", { min: null }).filters.ret_20d).toBeUndefined();
  });
});

describe("sort toggling", () => {
  it("new column starts at its better-first direction, same column flips", () => {
    expect(toggleSort({ sort: "composite", order: "desc" }, "flow")).toEqual({ sort: "flow", order: "desc" });
    expect(toggleSort({ sort: "flow", order: "desc" }, "flow")).toEqual({ sort: "flow", order: "asc" });
    expect(toggleSort({ sort: "flow", order: "asc" }, "flow")).toEqual({ sort: "flow", order: "desc" });
    expect(toggleSort({ sort: "composite", order: "desc" }, "volatility_20d", false)).toEqual({ sort: "volatility_20d", order: "asc" });
  });
});

describe("value formatting by meta unit", () => {
  const spec = (key: string) => {
    const metric = META.metrics.find((m) => m.key === key)!;
    return { key, unit: metric.unit, decimals: metric.decimals };
  };
  it("formats units", () => {
    expect(formatScreenerValue(12345, spec("total_net_20d"))).toBe("+12,345 張");
    expect(formatScreenerValue(-50210, spec("total_net_20d"))).toBe("−50,210 張");
    expect(formatScreenerValue(4.84, spec("inst_flow_ratio_20d"))).toBe("+4.8%");
    expect(formatScreenerValue(24.34, spec("volatility_20d"))).toBe("24.3%");
    expect(formatScreenerValue(-9.8, spec("max_drawdown_60d"))).toBe("-9.8%");
    expect(formatScreenerValue(-12, spec("foreign_streak"))).toBe("−12 日");
    expect(formatScreenerValue(0.18, spec("cmf_20"))).toBe("+0.18");
    expect(formatScreenerValue(9.1e10, spec("avg_turnover_20d"))).toBe("910 億");
    expect(formatScreenerValue(2e7, spec("avg_turnover_20d"))).toBe("2,000 萬");
  });
  it("shows missing values as an em dash and booleans as 是/否", () => {
    expect(formatScreenerValue(null, spec("ret_20d"))).toBe("—");
    expect(formatScreenerValue(undefined, spec("ret_20d"))).toBe("—");
    expect(formatScreenerValue(NaN, spec("ret_20d"))).toBe("—");
    expect(formatScreenerValue(true, spec("trend_aligned"))).toBe("是");
    expect(formatScreenerValue(false, spec("trend_aligned"))).toBe("否");
    expect(formatScreenerValue(0, spec("total_net_20d"))).toBe("0 張");
  });
  it("describes ranges", () => {
    expect(describeRange({ min: -8, max: 8 }, spec("ret_20d"))).toBe("-8.0% ~ 8.0%");
    expect(describeRange({ min: 2e7, max: null }, spec("avg_turnover_20d"))).toBe("≥ 2,000 萬");
    expect(describeRange({ min: 0.0001, max: null }, spec("inst_flow_ratio_20d"))).toBe("> 0");
    expect(describeRange({ min: null, max: 30 }, spec("volatility_20d"))).toBe("≤ 30.0%");
  });
});

describe("preset bool filters and metric kinds", () => {
  it("maps {equals:true} to flag= and keeps text metrics out of range filters and sorting", () => {
    const preset = { ...META.presets[3], filters: [{ key: "trend_aligned", equals: true }, { key: "inst_flow_ratio_20d", min: 0.0001 }] };
    const meta = { ...META, presets: [preset], metrics: [...META.metrics, { key: "revenue_period", label: "營收月份", group: "fundamental", unit: "", decimals: null, description: "", higher_is_better: null, kind: "text" as const }] };
    const eff = resolveEffective({ ...INITIAL_STATE, preset: preset.key }, meta);
    expect(eff.flags).toEqual(["trend_aligned"]);
    expect(eff.filters.inst_flow_ratio_20d).toEqual({ min: 0.0001, max: null });
    expect(resolveColumn(meta, "revenue_period").sortable).toBe(false);
    expect(resolveColumn(meta, "ret_20d").sortable).toBe(true);
    expect(formatScreenerValue("2026-10", { unit: "" })).toBe("2026-10");
  });
});

describe("columns and highlights", () => {
  it("shows the preset's columns, adding the sort key when it is not one of them", () => {
    const eff = resolveEffective(selectPreset(INITIAL_STATE, "money_flow"), META);
    expect(visibleColumns(eff)).toEqual(["flow", "total_net_20d", "inst_flow_ratio_20d", "foreign_streak", "trust_streak", "margin_change_20d_pct"]);
    const sorted = resolveEffective({ ...INITIAL_STATE, sort: "ret_60d" }, META);
    expect(visibleColumns(sorted)).toEqual(["momentum", "flow", "fundamental", "stability", "ret_60d"]);
  });
  it("folds highlights past three into a count", () => {
    expect(splitHighlights(["a", "b", "c", "d", "e"])).toEqual({ shown: ["a", "b", "c"], hidden: 2 });
    expect(splitHighlights([])).toEqual({ shown: [], hidden: 0 });
  });
});
