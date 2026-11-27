"use client";

import { type KeyboardEvent, useRef } from "react";
import type { ScreenerPreset } from "@/lib/api/types";

/** 策略快選: one card per preset from the meta, single choice (radiogroup, arrows move and select). */
export function PresetRow({
  presets,
  selected,
  onSelect,
}: {
  presets: ScreenerPreset[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const index = presets.findIndex((p) => p.key === selected);

  const onKeyDown = (event: KeyboardEvent) => {
    const last = presets.length - 1;
    const current = Math.max(index, 0);
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = current === last ? 0 : current + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = current === 0 ? last : current - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    onSelect(presets[next].key);
    ref.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };

  return (
    <section className="sc-presets-wrap" aria-labelledby="sc-presets-title">
      <h2 id="sc-presets-title" className="field-label">策略快選</h2>
      <div ref={ref} className="sc-presets" role="radiogroup" aria-labelledby="sc-presets-title" onKeyDown={onKeyDown}>
        {presets.map((preset, i) => {
          const checked = preset.key === selected;
          return (
            <button
              key={preset.key}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked || (index < 0 && i === 0) ? 0 : -1}
              className="sc-preset"
              onClick={() => onSelect(preset.key)}
            >
              <span className="sc-preset-label">{preset.label}</span>
              <span className="sc-preset-desc">{preset.description}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
