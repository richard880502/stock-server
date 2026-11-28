"use client";

import { useEffect, useId, useRef, useState } from "react";
import { isCommittableAsOf } from "@/lib/as-of-commit";
import { useDashboard } from "@/lib/dashboard-context";
import { today } from "@/lib/format";
import { ChevronDownIcon } from "./icons";

/** How long a typed date must stay unchanged before it is applied. */
export const AS_OF_COMMIT_DELAY_MS = 400;

/**
 * The analysis conditions that used to sit in the global query bar: the data cut-off date and,
 * where relative strength matters, the benchmark. A compact chip in a page heading; it reads
 * "截至今日" normally and turns into a warning-toned date when the viewer is looking at the past.
 *
 * The date field keeps its own draft: a native date input reports intermediate values while a
 * year is typed, so the shared query only changes once the draft is a finished date no later
 * than today and has settled (or on Enter / leaving the field).
 */
export function AsOfControl({ withBenchmark = false }: { withBenchmark?: boolean }) {
  const { query, refresh } = useDashboard();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const dateRef = useRef<HTMLInputElement>(null);
  const id = useId();
  const now = today();
  const historical = query.asOf !== now;

  const [draft, setDraft] = useState(query.asOf);
  const [syncedAsOf, setSyncedAsOf] = useState(query.asOf);
  if (syncedAsOf !== query.asOf) {
    setSyncedAsOf(query.asOf);
    setDraft(query.asOf);
  }

  const latest = useRef({ refresh, asOf: query.asOf });
  useEffect(() => {
    latest.current = { refresh, asOf: query.asOf };
  });
  const commit = (text: string) => {
    if (isCommittableAsOf(text, today()) && text !== latest.current.asOf) {
      latest.current.refresh({ asOf: text });
    }
  };

  useEffect(() => {
    if (!isCommittableAsOf(draft, today()) || draft === latest.current.asOf) return;
    const timer = window.setTimeout(() => commit(draft), AS_OF_COMMIT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [draft]);

  // Focus moves into the popover when it opens and back to the chip on Esc / toggle.
  useEffect(() => {
    if (open) dateRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      chipRef.current?.focus();
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
        ref={chipRef}
        type="button"
        className="asof-chip"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        aria-haspopup="dialog"
        onClick={() => {
          if (open) chipRef.current?.focus();
          setOpen((value) => !value);
        }}
      >
        <span className="asof-chip-label">資料截止</span>
        <strong className="asof-chip-value num">{historical ? query.asOf : "今日"}</strong>
        <ChevronDownIcon size={14} />
      </button>
      {historical && (
        <button type="button" className="button small asof-reset" onClick={() => refresh({ asOf: now })}>
          回到今天
        </button>
      )}
      {open && (
        <div className="popover asof-panel" id={`${id}-panel`} role="dialog" aria-label="資料截止日設定">
          <label className="field-label" htmlFor={`${id}-date`}>
            資料截止日
          </label>
          <input
            ref={dateRef}
            id={`${id}-date`}
            className="input"
            type="date"
            max={now}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => commit(draft)}
            onKeyDown={(event) => {
              if (event.key === "Enter") commit(draft);
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
