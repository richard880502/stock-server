"use client";

import { type KeyboardEvent, useEffect, useRef } from "react";
import type { ScreenerPreset } from "@/lib/api/types";

/** 策略快選: one scrollable row of chips from the meta, single choice (radiogroup, arrows move and select). */
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

  // Keep the selected chip visible: on phones it can sit off-screen after load or a deep link.
  useEffect(() => {
    const chip = ref.current?.querySelectorAll<HTMLElement>("button")[index];
    const row = ref.current;
    if (!chip || !row) return;
    const left = chip.offsetLeft - (row.clientWidth - chip.offsetWidth) / 2;
    row.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }, [index]);

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

  const description = presets[index]?.description;
  return (
    <section className="sc-presets-wrap" aria-labelledby="sc-presets-title">
      <h2 id="sc-presets-title" className="sr-only">策略快選</h2>
      <div ref={ref} className="sc-presets" role="radiogroup" aria-labelledby="sc-presets-title" onKeyDown={onKeyDown}>
        {presets.map((preset, i) => (
          <button
            key={preset.key}
            type="button"
            role="radio"
            aria-checked={preset.key === selected}
            tabIndex={preset.key === selected || (index < 0 && i === 0) ? 0 : -1}
            className="sc-preset"
            onClick={() => onSelect(preset.key)}
          >
            {preset.label}
          </button>
        ))}
      </div>
      {description && <p className="sc-preset-desc">{description}</p>}
    </section>
  );
}
