"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { stockHref } from "@/lib/stock-routes";
import { SymbolCombobox } from "./symbol-combobox";

/**
 * The "標的" line of pages that act on the shared symbol (回測, 預測): the stock's code and
 * name, a picker to change it in place, and a link to its page.
 */
export function TargetLine({ children }: { children?: ReactNode }) {
  const { query, refresh, recentSymbols, instrumentName } = useDashboard();
  const name = instrumentName?.symbol === query.symbol ? instrumentName.name : "";
  return (
    <div className="target-line" role="group" aria-label="分析標的">
      <SymbolCombobox
        label="標的"
        value={query.symbol}
        recents={recentSymbols}
        onSelect={(symbol) => refresh({ symbol })}
      />
      <p className="target-line-name" aria-label="股票名稱">
        <strong>{name || "—"}</strong>
        <span>{query.symbol.endsWith(".TWO") ? "上櫃" : "上市"}</span>
      </p>
      <Link className="button small" href={stockHref(query.symbol)}>
        個股頁 →
      </Link>
      {children}
    </div>
  );
}
