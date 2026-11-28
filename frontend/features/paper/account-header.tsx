"use client";

import type { AccountDetail, AccountSummary } from "@/lib/api/paper";
import { type SettingsFocus } from "./account-forms";
import {
  discountLabel,
  formatClock,
  formatMoney,
  formatSignedMoney,
  formatSignedPercent,
  toneClass,
} from "./paper-math";
import { Figure, PaperMenu, SimBadge } from "./paper-ui";

export type AccountActions = {
  onSelect: (id: string) => void;
  onNew: () => void;
  onSettings: (focus: SettingsFocus) => void;
  onReset: () => void;
  onArchive: () => void;
};

/** Header strip: account switcher, 「模擬」 badge, and the four figures that matter. */
export function AccountHeader({
  accounts,
  maxAccounts,
  account,
  detail,
  stale,
  actions,
}: {
  accounts: AccountSummary[];
  maxAccounts: number;
  account: AccountSummary;
  detail: AccountDetail | null;
  /** Figures belong to another account / are refetching: dim them. */
  stale: boolean;
  actions: AccountActions;
}) {
  const figures = detail ?? account;
  const canAdd = accounts.length < maxAccounts;
  return (
    <header className="paper-header card" aria-label="模擬帳戶">
      <div className="paper-header-top">
        <h1 className="paper-title">模擬交易</h1>
        <SimBadge />
        <PaperMenu
          className="paper-account-switch"
          label={`切換帳戶，目前 ${account.name}`}
          trigger={<span className="paper-account-name">{account.name}</span>}
          items={[
            ...accounts.map((item) => ({
              key: item.id,
              label: item.name,
              note: formatMoney(item.equity),
              selected: item.id === account.id,
              onSelect: () => actions.onSelect(item.id),
            })),
            "separator" as const,
            {
              key: "new",
              label: canAdd ? "新增帳戶" : `新增帳戶（已達 ${maxAccounts} 個上限）`,
              onSelect: canAdd ? actions.onNew : () => {},
            },
          ]}
        />
        <PaperMenu
          className="paper-account-menu"
          align="end"
          label="帳戶選項"
          trigger={<span>帳戶選項</span>}
          items={[
            { key: "rename", label: "改名", onSelect: () => actions.onSettings("name") },
            {
              key: "discount",
              label: "手續費折扣",
              note: discountLabel(account.commission_discount),
              onSelect: () => actions.onSettings("discount"),
            },
            {
              key: "daytrade",
              label: "允許現股當沖",
              note: account.allow_day_trade ? "開" : "關",
              onSelect: () => actions.onSettings("daytrade"),
            },
            "separator",
            { key: "reset", label: "重設帳戶…", danger: true, onSelect: actions.onReset },
            { key: "archive", label: "封存帳戶…", danger: true, onSelect: actions.onArchive },
          ]}
        />
        <p className="paper-asof num" aria-live="polite">
          價格時間 {formatClock(figures.prices_as_of)}
        </p>
      </div>
      <dl className={`paper-figures${stale ? " is-refreshing" : ""}`}>
        <Figure label="總權益">{formatMoney(figures.equity)}</Figure>
        <Figure
          label="今日損益"
          className={toneClass(figures.day_pnl)}
          sub={formatSignedPercent(figures.day_return_pct)}
        >
          {formatSignedMoney(figures.day_pnl)}
        </Figure>
        <Figure
          label="累計損益"
          className={toneClass(figures.total_pnl)}
          sub={formatSignedPercent(figures.total_return_pct)}
        >
          {formatSignedMoney(figures.total_pnl)}
        </Figure>
        <Figure label="可用資金">{formatMoney(figures.available_cash)}</Figure>
        {figures.dividend_income != null && figures.dividend_income > 0 && (
          <Figure label="累計股利" className={toneClass(figures.dividend_income)}>
            {formatSignedMoney(figures.dividend_income)}
          </Figure>
        )}
      </dl>
    </header>
  );
}

export function AccountHeaderSkeleton() {
  return (
    <div className="paper-header card" aria-busy="true" aria-label="正在載入模擬帳戶">
      <div className="paper-header-top">
        <h1 className="paper-title">模擬交易</h1>
        <SimBadge />
      </div>
      <div className="paper-figures">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="paper-figure">
            <span className="skeleton line" style={{ width: "40%" }} />
            <span className="skeleton line" style={{ width: "70%", height: "1.5rem", marginTop: "0.4rem" }} />
          </div>
        ))}
      </div>
    </div>
  );
}
