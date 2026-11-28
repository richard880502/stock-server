"use client";

import Link from "next/link";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { formatNumber, formatSigned } from "@/lib/format";
import type { PaperSummary } from "./today-model";
import { CardSkeleton, MoreLink } from "./today-parts";

const twd = (value: number | null) => (value === null ? "—" : `NT$ ${formatNumber(value, 0)}`);
const count = (value: number | null) => (value === null ? "—" : String(value));

/** `summary` null = the user has no account yet. Loading is the caller's job. */
export function PaperBody({ summary }: { summary: PaperSummary | null }) {
  if (!summary) {
    return (
      <>
        <p className="note">用虛擬資金練習下單，驗證看盤與選股的想法；不會有真實交易。</p>
        <Link href="/paper" className="button primary today-cta">
          開始模擬交易
        </Link>
      </>
    );
  }
  return (
    <>
      <p className="today-meta">
        {summary.name}
        {summary.accountCount > 1 ? `（共 ${summary.accountCount} 個帳戶）` : ""}
      </p>
      <dl className="today-headlines">
        <div className="today-headline">
          <dt>帳戶權益</dt>
          <dd className="num">{twd(summary.equity)}</dd>
        </div>
        <div className="today-headline">
          <dt>今日損益</dt>
          <dd className="num">
            {summary.dayPnl === null ? (
              "—"
            ) : (
              <Direction value={summary.dayPnl}>{formatSigned(summary.dayPnl, 0)}</Direction>
            )}
          </dd>
        </div>
        <div className="today-headline">
          <dt>持股 / 委託中</dt>
          <dd className="num">
            {count(summary.positions)} 檔 / {count(summary.workingOrders)} 筆
          </dd>
        </div>
      </dl>
      <MoreLink href="/paper">前往模擬交易 →</MoreLink>
    </>
  );
}

/** The card shell; `state` is "loading" while the one accounts call is in flight. */
export function PaperCard({ loading, summary }: { loading: boolean; summary: PaperSummary | null }) {
  return (
    <Card title="模擬交易" className="today-card today-paper">
      {loading ? <CardSkeleton label="模擬交易載入中" lines={3} /> : <PaperBody summary={summary} />}
    </Card>
  );
}
