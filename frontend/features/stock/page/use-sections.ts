"use client";

import { useEffect, useRef, useState } from "react";
import type { StockSection } from "@/lib/stock-routes";
import { pickActiveSection, SECTION_LINKS, sectionFromHash } from "./model";

/** Where the sticky bars end, in px from the viewport top: topbar + section nav (see stock-page.css). */
function readLine(): number {
  const nav = document.querySelector<HTMLElement>(".stock-nav");
  const rect = nav?.getBoundingClientRect();
  return (rect ? rect.bottom : 0) + 24;
}

/**
 * The section nearest the top of the viewport. Reads section tops on scroll (one frame at a
 * time) rather than observing intersections: with sections of very different heights the
 * "last one that passed the line" rule is simpler and cannot flicker between two states.
 */
export function useActiveSection(enabled: boolean): StockSection | null {
  const [active, setActive] = useState<StockSection | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const tops: Partial<Record<StockSection, number | null>> = {};
      for (const { id } of SECTION_LINKS) tops[id] = document.getElementById(id)?.getBoundingClientRect().top ?? null;
      const root = document.documentElement;
      const atBottom = window.innerHeight + window.scrollY >= root.scrollHeight - 4;
      setActive(pickActiveSection(tops, readLine(), atBottom));
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    schedule();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [enabled]);
  return enabled ? active : null;
}

/**
 * The browser scrolls to `#chart` on load before the sections exist (they render after the signal
 * arrives), and never to `#profile=earnings` at all. Once the sections are on the page, bring the
 * hash's section into view, once per stock.
 */
export function useHashScroll(ready: boolean, symbol: string) {
  // `ready` also flips when the same stock reloads (as-of change, retry): do not jump again then.
  const done = useRef<string | null>(null);
  useEffect(() => {
    if (!ready || done.current === symbol) return;
    done.current = symbol;
    const section = sectionFromHash(window.location.hash);
    if (!section) return;
    const timer = window.setTimeout(() => document.getElementById(section)?.scrollIntoView({ block: "start" }), 50);
    return () => window.clearTimeout(timer);
  }, [ready, symbol]);
}
