"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export const VIEWS = [
  { href: "/", label: "今日訊號" },
  { href: "/backtest", label: "策略回測" },
  { href: "/forecast", label: "預測實驗室" },
] as const;

export function ViewTabs() {
  const pathname = usePathname() ?? "/";
  const current = pathname.replace(/\/+$/, "") || "/";
  return (
    <nav className="view-tabs" aria-label="控制台檢視">
      {VIEWS.map((view) => {
        const active = current === view.href;
        return (
          <Link
            key={view.href}
            href={view.href}
            className={active ? "active" : ""}
            aria-current={active ? "page" : undefined}
          >
            {view.label}
          </Link>
        );
      })}
    </nav>
  );
}
