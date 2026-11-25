"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { ChevronDownIcon } from "./icons";
import { SymbolCombobox } from "./symbol-combobox";

export const QUERY_FORM_ID = "query-form";

/** Focus the symbol field of the full query form (it must be visible). */
export function focusSymbolField() {
  const input = document.querySelector<HTMLInputElement>(`#${QUERY_FORM_ID} input[role="combobox"]`);
  input?.focus();
  input?.select();
}

/**
 * One-line digest of the current query. Fixed under the topbar once the full form has scrolled
 * away (desktop); the only visible form on phones until expanded.
 */
export function QuerySummary({
  visible,
  expanded,
  onEdit,
}: {
  visible: boolean;
  expanded: boolean;
  onEdit: () => void;
}) {
  const { query, instrumentName } = useDashboard();
  const name = instrumentName?.symbol === query.symbol ? instrumentName.name : "";
  return (
    <div className="query-summary" data-visible={visible} aria-label="目前分析條件" role="group">
      <p className="query-summary-text">
        <strong className="mono">{query.symbol}</strong>
        {name && <span className="query-summary-name"> {name}</span>}
        <span className="query-summary-meta num">
          {" · "}截止 {query.asOf}
          {query.benchmark ? ` · ${query.benchmark}` : ""}
        </span>
      </p>
      <button
        type="button"
        className="button small query-summary-edit"
        aria-expanded={expanded}
        aria-controls={QUERY_FORM_ID}
        onClick={onEdit}
      >
        {expanded ? "收合" : "修改"}
      </button>
    </div>
  );
}

/** Symbol / as-of date / benchmark shared by every view. */
export function QueryBar({ expanded }: { expanded: boolean }) {
  const { query, signalData, refresh, recentSymbols } = useDashboard();
  const [benchmarkOpen, setBenchmarkOpen] = useState(false);
  const benchmarkRef = useRef<HTMLDivElement>(null);
  const benchmarkId = useId();

  useEffect(() => {
    if (!benchmarkOpen) return;
    const onPointer = (event: PointerEvent) => {
      if (!benchmarkRef.current?.contains(event.target as Node)) setBenchmarkOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setBenchmarkOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [benchmarkOpen]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    refresh();
  };

  return (
    <form
      id={QUERY_FORM_ID}
      className="query-bar"
      data-expanded={expanded}
      aria-label="分析條件"
      onSubmit={submit}
    >
      <SymbolCombobox
        value={query.symbol}
        recents={recentSymbols}
        onSelect={(symbol) => refresh({ symbol })}
      />
      <div className="field">
        <label className="field-label" htmlFor={`${benchmarkId}-date`}>
          資料截止日
        </label>
        <input
          id={`${benchmarkId}-date`}
          className="input"
          type="date"
          value={query.asOf}
          onChange={(event) => query.setAsOf(event.target.value)}
        />
      </div>
      <div className="field benchmark" ref={benchmarkRef}>
        <span className="field-label" id={`${benchmarkId}-label`}>
          比較基準
        </span>
        <button
          type="button"
          className="input benchmark-button"
          aria-expanded={benchmarkOpen}
          aria-controls={`${benchmarkId}-panel`}
          aria-labelledby={`${benchmarkId}-label ${benchmarkId}-value`}
          onClick={() => setBenchmarkOpen((open) => !open)}
        >
          <span id={`${benchmarkId}-value`} className="mono">
            {query.benchmark || "無"}
          </span>
          <ChevronDownIcon size={16} />
        </button>
        {benchmarkOpen && (
          <div className="popover" id={`${benchmarkId}-panel`}>
            <label className="field-label" htmlFor={`${benchmarkId}-input`}>
              基準代號
            </label>
            <input
              id={`${benchmarkId}-input`}
              className="input mono"
              value={query.benchmark}
              onChange={(event) => query.setBenchmark(event.target.value.toUpperCase())}
              placeholder="^TWII"
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
            <p className="field-hint">用於相對強弱因子。上市預設 ^TWII，上櫃預設 ^TWOII。</p>
          </div>
        )}
      </div>
      <button className="button primary submit" disabled={signalData.loading}>
        {signalData.loading && <span className="spinner small" aria-hidden="true" />}
        <span className="submit-short">更新</span>
        <span className="submit-long">{signalData.loading ? "更新中" : "更新訊號"}</span>
      </button>
    </form>
  );
}
