"use client";

import { type StockSection } from "@/lib/stock-routes";
import { SECTION_LINKS } from "./model";

/** Slim sticky row of anchors to the page's sections; the one in view is marked. */
export function SectionNav({ active, etf = false }: { active: StockSection | null; etf?: boolean }) {
  return (
    <nav className="stock-nav" aria-label="頁面段落">
      {SECTION_LINKS.filter(({ id }) => !etf || id === "summary" || id === "profile").map(({ id, label }) => (
        <a key={id} href={`#${id}`} aria-current={active === id ? "location" : undefined}>
          {label}
        </a>
      ))}
    </nav>
  );
}
