"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartIcon, FlaskIcon, MarketIcon, PulseIcon, StarIcon, TerminalIcon } from "./icons";

export const VIEWS = [
  { href: "/", label: "市場與訊號", Icon: PulseIcon },
  { href: "/market", label: "市場深入", Icon: MarketIcon },
  { href: "/watchlist", label: "自選股", Icon: StarIcon },
  { href: "/terminal", label: "看盤", Icon: TerminalIcon },
  { href: "/backtest", label: "策略回測", Icon: ChartIcon },
  { href: "/forecast", label: "預測實驗室", Icon: FlaskIcon },
] as const;

/** Inline tabs in the top bar on wide screens; a fixed bottom bar on phones (see layout.css). */
export function ViewTabs() {
  const pathname = usePathname() ?? "/";
  const current = pathname.replace(/\/+$/, "") || "/";
  return (
    <nav className="view-tabs" aria-label="控制台檢視">
      {VIEWS.map(({ href, label, Icon }) => {
        const active = current === href;
        return (
          <Link
            key={href}
            href={href}
            className="view-tab"
            aria-current={active ? "page" : undefined}
          >
            <Icon size={20} />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
