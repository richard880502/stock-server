"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDownIcon, ScreenerIcon, SearchIcon, XIcon } from "@/components/icons";
import { SegmentedControl } from "@/components/segmented-control";
import type { ScreenerMeta, ScreenerMetric } from "@/lib/api/types";
import { formatTwdCompact } from "@/lib/format";
import { DebouncedInput } from "./debounced-input";
import {
  clearFilters,
  describeRange,
  type Effective,
  isBoolMetric,
  isNumberMetric,
  type RangeFilter,
  resolveColumn,
  SCREENER_MARKETS,
  type ScreenerState,
  setFlag,
  setRange,
  TURNOVER_KEY,
  TURNOVER_OPTIONS,
} from "./screener-model";

export type Edit = (change: (state: ScreenerState, effective: Effective) => ScreenerState) => void;

const toNumber = (text: string): number | null => {
  const value = Number(text);
  return text.trim() === "" || !Number.isFinite(value) ? null : value;
};
const isNumberText = (text: string) => text.trim() === "" || toNumber(text) !== null;
const sameNumber = (a: string, b: string) => toNumber(a) === toNumber(b);

/* ---- Industry multi-select ------------------------------------------------- */

function IndustryPicker({
  industries,
  selected,
  onChange,
}: {
  industries: ScreenerMeta["industries"];
  selected: string[];
  onChange: (codes: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    const onPointer = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const needle = search.trim().toLowerCase();
  const shown = needle
    ? industries.filter((item) => item.name.toLowerCase().includes(needle) || item.code.includes(needle))
    : industries;
  const toggle = (code: string) =>
    onChange(selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code]);

  return (
    <div className="sc-picker" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="input sc-picker-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{selected.length === 0 ? "全部產業" : `已選 ${selected.length} 個產業`}</span>
        <ChevronDownIcon size={16} />
      </button>
      {open && (
        <div id={panelId} className="sc-popover" role="dialog" aria-label="選擇產業">
          <label className="sc-picker-search">
            <SearchIcon size={16} />
            <span className="sr-only">搜尋產業</span>
            <input
              autoFocus
              type="search"
              placeholder="搜尋產業"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <ul className="sc-picker-list">
            {shown.map((item) => (
              <li key={item.code}>
                <label className="sc-check">
                  <input type="checkbox" checked={selected.includes(item.code)} onChange={() => toggle(item.code)} />
                  <span className="sc-check-label">{item.name}</span>
                  <span className="num text-muted">{item.count}</span>
                </label>
              </li>
            ))}
            {shown.length === 0 && <li className="note">找不到符合的產業</li>}
          </ul>
          <div className="sc-picker-foot">
            <button type="button" className="button small" disabled={selected.length === 0} onClick={() => onChange([])}>
              清除
            </button>
            <button type="button" className="button small primary" onClick={() => setOpen(false)}>
              完成
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---- Advanced filters ------------------------------------------------------ */

function RangeRow({
  metric,
  range,
  onChange,
}: {
  metric: ScreenerMetric;
  range: RangeFilter | undefined;
  onChange: (patch: Partial<RangeFilter>) => void;
}) {
  const unit = metric.unit === "" ? null : metric.unit;
  return (
    <div className="sc-range">
      <span className="sc-range-label" title={metric.description || undefined}>
        {metric.label}
        {unit && <span className="text-muted">（{unit}）</span>}
      </span>
      <span className="sc-range-inputs">
        {(["min", "max"] as const).map((side) => (
          <DebouncedInput
            key={side}
            className="input sc-range-input num"
            inputMode="decimal"
            placeholder={side === "min" ? "最小" : "最大"}
            aria-label={`${metric.label}${side === "min" ? "下限" : "上限"}`}
            value={range?.[side] == null ? "" : String(range[side])}
            acceptText={isNumberText}
            same={sameNumber}
            onCommit={(text) => onChange({ [side]: toNumber(text) })}
          />
        ))}
      </span>
    </div>
  );
}

function AdvancedFilters({ meta, effective, edit }: { meta: ScreenerMeta; effective: Effective; edit: Edit }) {
  const groups = meta.groups
    .filter((group) => group.key !== "score")
    .map((group) => ({ group, metrics: meta.metrics.filter((metric) => metric.group === group.key) }))
    .filter((item) => item.metrics.length > 0);
  return (
    <div className="sc-advanced" role="group" aria-label="進階篩選">
      {groups.map(({ group, metrics }) => (
        <fieldset key={group.key} className="sc-adv-group">
          <legend>{group.label}</legend>
          {metrics.filter((metric) => isBoolMetric(metric) || isNumberMetric(metric)).map((metric) =>
            isBoolMetric(metric) ? (
              <label key={metric.key} className="sc-check" title={metric.description || undefined}>
                <input
                  type="checkbox"
                  checked={effective.flags.includes(metric.key)}
                  onChange={(event) => edit((s, e) => setFlag(s, e, metric.key, event.target.checked))}
                />
                <span className="sc-check-label">只看「{metric.label}」</span>
              </label>
            ) : (
              <RangeRow
                key={metric.key}
                metric={metric}
                range={effective.filters[metric.key]}
                onChange={(patch) => edit((s, e) => setRange(s, e, metric.key, patch))}
              />
            ),
          )}
        </fieldset>
      ))}
    </div>
  );
}

/* ---- Bar ------------------------------------------------------------------- */

type Chip = { id: string; text: string; remove: () => void };

export function FilterBar({
  meta,
  state,
  effective,
  edit,
}: {
  meta: ScreenerMeta;
  state: ScreenerState;
  effective: Effective;
  edit: Edit;
}) {
  const [advanced, setAdvanced] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const bodyId = useId();
  const turnover = effective.filters[TURNOVER_KEY]?.min ?? null;
  const turnoverOptions = TURNOVER_OPTIONS.some((o) => o.value === turnover) || turnover === null
    ? TURNOVER_OPTIONS
    : [...TURNOVER_OPTIONS, { value: turnover, label: formatTwdCompact(turnover) }];

  const chips = useMemo<Chip[]>(() => {
    const result: Chip[] = [];
    for (const [key, range] of Object.entries(effective.filters)) {
      const column = resolveColumn(meta, key);
      result.push({
        id: `f:${key}`,
        text: `${column.label} ${describeRange(range, column)}`,
        remove: () => edit((s, e) => setRange(s, e, key, { min: null, max: null })),
      });
    }
    for (const key of effective.flags) {
      result.push({
        id: `b:${key}`,
        text: resolveColumn(meta, key).label,
        remove: () => edit((s, e) => setFlag(s, e, key, false)),
      });
    }
    for (const code of state.industries) {
      const name = meta.industries.find((item) => item.code === code)?.name ?? code;
      result.push({ id: `i:${code}`, text: name, remove: () => edit((s) => ({ ...s, industries: s.industries.filter((c) => c !== code) })) });
    }
    if (state.q.trim()) {
      result.push({ id: "q", text: `搜尋「${state.q.trim()}」`, remove: () => edit((s) => ({ ...s, q: "" })) });
    }
    return result;
  }, [effective, state.industries, state.q, meta, edit]);

  return (
    <section className="card sc-filters" aria-label="篩選條件">
      <button
        type="button"
        className="sc-filters-toggle"
        aria-expanded={sheetOpen}
        aria-controls={bodyId}
        onClick={() => setSheetOpen((value) => !value)}
      >
        <span className="sc-filters-toggle-label">
          <ScreenerIcon size={16} />
          篩選
          {chips.length > 0 && <span className="sc-count num">{chips.length}</span>}
        </span>
        <ChevronDownIcon size={16} />
      </button>

      <div id={bodyId} className="sc-filters-body" data-open={sheetOpen}>
        <div className="sc-filter-row">
          <div className="control">
            <span className="field-label">市場</span>
            <SegmentedControl
              label="市場"
              value={state.market}
              options={SCREENER_MARKETS}
              onChange={(market) => edit((s) => ({ ...s, market }))}
            />
          </div>
          <div className="control">
            <span className="field-label">產業</span>
            <IndustryPicker
              industries={meta.industries}
              selected={state.industries}
              onChange={(industries) => edit((s) => ({ ...s, industries }))}
            />
          </div>
          <div className="control">
            <label className="field-label" htmlFor={`${bodyId}-turnover`}>
              日均成交值下限
            </label>
            <select
              id={`${bodyId}-turnover`}
              className="input"
              value={turnover === null ? "" : String(turnover)}
              onChange={(event) => {
                const value = event.target.value === "" ? null : Number(event.target.value);
                edit((s, e) => setRange(s, e, TURNOVER_KEY, { min: value }));
              }}
            >
              {turnoverOptions.map((option) => (
                <option key={option.label} value={option.value === null ? "" : String(option.value)}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="control sc-search">
            <label className="field-label" htmlFor={`${bodyId}-q`}>
              代號或名稱
            </label>
            <DebouncedInput
              id={`${bodyId}-q`}
              type="search"
              className="input"
              placeholder="例：2330、台積"
              autoComplete="off"
              value={state.q}
              onCommit={(q) => edit((s) => ({ ...s, q }))}
            />
          </div>
          <label className="switch-row sc-risk">
            <input
              type="checkbox"
              checked={!state.includeRisk}
              onChange={(event) => edit((s) => ({ ...s, includeRisk: !event.target.checked }))}
            />
            <span className="switch-copy">
              排除處置股
              <small>處置中的股票交易受限</small>
            </span>
          </label>
          <button
            type="button"
            className="button sc-advanced-toggle"
            aria-expanded={advanced}
            onClick={() => setAdvanced((value) => !value)}
          >
            進階篩選
            <ChevronDownIcon size={16} />
          </button>
        </div>

        {advanced && <AdvancedFilters meta={meta} effective={effective} edit={edit} />}

        {chips.length > 0 && (
          <div className="sc-chips" role="group" aria-label="目前的篩選條件">
            {chips.map((chip) => (
              <span key={chip.id} className="sc-filter-chip">
                {chip.text}
                <button type="button" aria-label={`移除：${chip.text}`} onClick={chip.remove}>
                  <XIcon size={12} />
                </button>
              </span>
            ))}
            <button type="button" className="button small" onClick={() => edit(clearFilters)}>
              清除全部
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
