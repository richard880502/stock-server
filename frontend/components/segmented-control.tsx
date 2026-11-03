"use client";

import { type KeyboardEvent, useRef } from "react";

export type SegmentOption<T extends string | number> = { value: T; label: string };

/**
 * Single-choice control rendered as a pill of buttons (ARIA radiogroup with a
 * roving tabindex: Tab enters the group, arrows move and select).
 */
export function SegmentedControl<T extends string | number>({
  label,
  value,
  options,
  onChange,
  wrap = false,
}: {
  /** Accessible name of the group. */
  label: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
  /** Let the options wrap onto several lines on narrow screens. */
  wrap?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const selected = options.findIndex((option) => option.value === value);

  const onKeyDown = (event: KeyboardEvent) => {
    const last = options.length - 1;
    const current = selected < 0 ? 0 : selected;
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = current === last ? 0 : current + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = current === 0 ? last : current - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    onChange(options[next].value);
    ref.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };

  return (
    <div
      ref={ref}
      className={`segmented${wrap ? " wrap" : ""}`}
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {options.map((option, index) => {
        const checked = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked || (selected < 0 && index === 0) ? 0 : -1}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
