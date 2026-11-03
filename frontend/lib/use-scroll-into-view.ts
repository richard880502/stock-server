"use client";

import { useEffect } from "react";

/**
 * On single-column (phone / narrow) layouts the result sits below its form; when
 * `active` turns true, bring the element with `id` into view. Respects
 * prefers-reduced-motion.
 */
export function useScrollIntoViewOnStart(active: boolean, id: string) {
  useEffect(() => {
    if (!active || !window.matchMedia("(max-width: 899px)").matches) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById(id)
      ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [active, id]);
}
