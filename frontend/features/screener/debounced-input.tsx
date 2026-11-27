"use client";

import { type InputHTMLAttributes, useEffect, useRef, useState } from "react";

/**
 * A text input that keeps its own draft and reports it ~300 ms after the last keystroke (or on
 * blur). The draft is only replaced from outside when the outside value genuinely differs from
 * what was last reported, so the URL echoing a value back never eats characters being typed.
 * `acceptText` lets a field hold back text that is not a value yet (a lone "-"); `same` compares
 * values by meaning ("20" and "20.").
 */
export function DebouncedInput({
  value,
  onCommit,
  delay = 300,
  acceptText = () => true,
  same = (a, b) => a === b,
  ...props
}: {
  value: string;
  onCommit: (value: string) => void;
  delay?: number;
  acceptText?: (text: string) => boolean;
  same?: (a: string, b: string) => boolean;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur">) {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(value);
  const [reported, setReported] = useState(value);
  const timer = useRef<number | undefined>(undefined);
  const pending = useRef<string | null>(null);

  if (seen !== value) {
    setSeen(value);
    if (!same(value, reported)) {
      setDraft(value);
      setReported(value);
    }
  }

  const flush = () => {
    window.clearTimeout(timer.current);
    const text = pending.current;
    pending.current = null;
    if (text === null || !acceptText(text)) return;
    setReported(text);
    onCommit(text);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <input
      {...props}
      value={draft}
      onChange={(event) => {
        setDraft(event.target.value);
        pending.current = event.target.value;
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(flush, delay);
      }}
      onBlur={flush}
      onKeyDown={(event) => {
        if (event.key === "Enter") flush();
        props.onKeyDown?.(event);
      }}
    />
  );
}
