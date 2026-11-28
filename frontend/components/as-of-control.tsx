"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { today } from "@/lib/format";
import { ChevronDownIcon } from "./icons";

/**
 * The analysis conditions that used to sit in the global query bar: the data cut-off date and,
 * where relative strength matters, the benchmark. A compact chip in a page heading; it reads
 * "截至今日" normally and turns into a warning-toned date when the viewer is looking at the past.
 */
export function AsOfControl({ withBenchmark = false }: { withBenchmark?: boolean }) {
  const { query, refresh } = useDashboard();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const id = useId();
  const now = today();
  const historical = query.asOf !== now;

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="asof-control" ref={rootRef} data-historical={historical || undefined}>
      <button
        type="button"
        className="asof-chip"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{historical ? `歷史日期 ${query.asOf}` : "截至今日"}</span>
        <ChevronDownIcon size={14} />
      </button>
      {historical && (
        <button type="button" className="asof-reset" onClick={() => refresh({ asOf: now })}>
          回到今天
        </button>
      )}
      {open && (
        <div className="popover asof-panel" id={`${id}-panel`}>
          <label className="field-label" htmlFor={`${id}-date`}>
            資料截止日
          </label>
          <input
            id={`${id}-date`}
            className="input"
            type="date"
            max={now}
            value={query.asOf}
            onChange={(event) => {
              if (/^\d{4}-\d{2}-\d{2}$/.test(event.target.value)) refresh({ asOf: event.target.value });
            }}
          />
          <p className="field-hint">只使用這一天收盤後已公布的資料，可用來回看當時的判斷。</p>
          {withBenchmark && (
            <>
              <label className="field-label" htmlFor={`${id}-benchmark`}>
                比較基準
              </label>
              <input
                id={`${id}-benchmark`}
                className="input mono"
                defaultValue={query.benchmark}
                key={query.benchmark}
                placeholder="^TWII"
                autoComplete="off"
                spellCheck={false}
                onBlur={(event) => {
                  const next = event.target.value.trim().toUpperCase();
                  if (next !== query.benchmark) refresh({ benchmark: next });
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") (event.target as HTMLInputElement).blur();
                }}
              />
              <p className="field-hint">用於相對強弱因子。上市預設 ^TWII，上櫃預設 ^TWOII。</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
