import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/watchlist-star", () => ({
  WatchlistStar: ({ symbol }: { symbol: string }) => createElement("span", { "data-star": symbol }),
}));

import { ENTRIES, META } from "./fixtures";
import { ResultCards, ResultsTable, type ResultsProps } from "./results";
import { RowDetail } from "./row-detail";
import { INITIAL_STATE, resolveColumn, resolveEffective, selectPreset, visibleColumns } from "./screener-model";

const props = (preset: string, overrides: Partial<ResultsProps> = {}): ResultsProps => {
  const effective = resolveEffective(selectPreset(INITIAL_STATE, preset), META);
  return {
    entries: ENTRIES,
    meta: META,
    columns: visibleColumns(effective).map((key) => resolveColumn(META, key)),
    sort: effective.sort,
    order: effective.order,
    expanded: new Set<string>(),
    refreshing: false,
    onToggle: () => {},
    onSort: () => {},
    onSignal: () => {},
    onBacktest: () => {},
    ...overrides,
  };
};
const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

describe("ResultsTable", () => {
  it("renders every fixture row with table semantics and the preset's columns", () => {
    const out = html(createElement(ResultsTable, props("money_flow")));
    expect(out).toContain("<table");
    expect(out.match(/<tr class="sc-row/g)).toHaveLength(ENTRIES.length);
    for (const label of ["資金流向", "法人 20 日合計買賣超", "外資連買賣", "融資 20 日增減"]) expect(out).toContain(label);
    expect(out).toContain("+12,345 張");
    expect(out).toContain("−50,210 張");
    expect(out).toContain("−12 日");
  });
  it("marks only the sorted header with aria-sort", () => {
    const out = html(createElement(ResultsTable, props("money_flow")));
    expect(out.match(/aria-sort="descending"/g)).toHaveLength(1);
    expect(out).toMatch(/aria-sort="descending"[^>]*class="sc-th-sortable"|class="sc-th-sortable"[^>]*aria-sort="descending"/);
    const flipped = html(createElement(ResultsTable, props("money_flow", { order: "asc" })));
    expect(flipped).toContain('aria-sort="ascending"');
  });
  it("shows missing values as — and never as 0, plus flags, the industry and a +n highlight chip", () => {
    const out = html(createElement(ResultsTable, props("money_flow")));
    expect(out).toContain("處置");
    expect(out).toContain("注意");
    expect(out).toContain("電腦及週邊設備業");
    expect(out).toContain("+1</li>"); // 台積電 has four highlights
    expect(out).toContain("名稱股份有限公司"); // a long name is kept whole (cut by CSS ellipsis)
    const cheapRow = out.slice(out.indexOf("2412"), out.indexOf("2881"));
    expect(cheapRow).toContain("—");
    expect(cheapRow).not.toMatch(/>0 張</);
  });
  it("renders the detail row for expanded symbols", () => {
    const out = html(createElement(ResultsTable, props("balanced", { expanded: new Set(["2330.TW"]) })));
    expect(out).toContain('id="sc-detail-2330.TW"');
    expect(out).toContain("查看訊號");
    expect(out).toContain("策略回測");
    expect(out).toContain('aria-expanded="true"');
  });
});

describe("ResultCards", () => {
  it("shows the composite and the preset's first three columns per card", () => {
    const out = html(createElement(ResultCards, props("money_flow")));
    expect(out.match(/class="sc-card"/g)).toHaveLength(ENTRIES.length);
    expect(out).toContain("法人 20 日合計買賣超");
    expect(out).toContain("資金流向");
    expect(out).toContain("法人 20 日買超佔成交量");
    expect(out).not.toContain("外資連買賣"); // 4th and later columns are left to the detail
  });
});

describe("RowDetail", () => {
  const detail = (index: number) =>
    html(createElement(RowDetail, { entry: ENTRIES[index], meta: META, onSignal: () => {}, onBacktest: () => {} }));
  it("shows scores with their recipes and metrics grouped by the meta groups", () => {
    const out = detail(0);
    expect(out).toContain("因子分數");
    expect(out).toContain("法人 20 日買超佔成交量 30%");
    expect(out).toContain("融資 20 日增減（越低越好） 15%");
    expect(out.match(/>綜合</g)).toHaveLength(1);
    for (const group of ["價格動能", "資金流向", "信用交易", "營收", "風險", "流動性"]) expect(out).toContain(group);
    expect(out).toContain("是"); // trend_aligned
    expect(out).toContain("910 億");
  });
  it("renders a stock without data: every missing score and metric is —", () => {
    const out = detail(5);
    expect(out).toContain("—");
    expect(out).not.toContain("NaN");
    expect(out).not.toContain("undefined");
  });
});
