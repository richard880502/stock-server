"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartIcon, FlaskIcon, MarketIcon, PulseIcon, ScreenerIcon, StarIcon } from "./icons";
import { PaperIcon } from "./paper-icon";

/** Primary navigation, in the order of the research journey. */
export const VIEWS = [
  { href: "/", label: "總覽", short: "總覽", Icon: PulseIcon },
  { href: "/market", label: "市場", short: "市場", Icon: MarketIcon },
  { href: "/screener", label: "選股", short: "選股", Icon: ScreenerIcon },
  { href: "/watchlist", label: "自選股", short: "自選", Icon: StarIcon },
  { href: "/backtest", label: "回測", short: "回測", Icon: ChartIcon },
  { href: "/paper", label: "模擬交易", short: "模擬", Icon: PaperIcon },
] as const;

/** Pages reached from a stock page or the command palette only; not in the nav. */
export const SECONDARY_VIEWS = [{ href: "/forecast", label: "預測", Icon: FlaskIcon }] as const;

/** Inline tabs in the top bar on wide screens; a fixed bottom bar on phones (see layout.css). */
export function ViewTabs() {
  const pathname = usePathname() ?? "/";
  const current = pathname.replace(/\/+$/, "") || "/";
  return (
    <nav className="view-tabs" aria-label="控制台檢視">
      {VIEWS.map(({ href, label, short, Icon }) => {
        const active = current === href;
        return (
          <Link
            key={href}
            href={href}
            className="view-tab"
            aria-label={label}
            aria-current={active ? "page" : undefined}
          >
            <Icon size={20} />
            <span className="view-tab-label">{label}</span>
            <span className="view-tab-short" aria-hidden="true">{short}</span>
          </Link>
        );
      })}
    </nav>
  );
}
