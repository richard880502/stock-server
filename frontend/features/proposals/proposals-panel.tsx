"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { Card } from "@/components/card";
import { ErrorBanner } from "@/components/error-banner";
import { formatDateTime } from "@/features/paper/paper-math";
import { SimBadge } from "@/features/paper/paper-ui";
import { usePagedList, usePolled } from "@/features/paper/use-paper-data";
import { listProposals, type Proposal } from "@/lib/api/proposals";
import { stockHref } from "@/lib/stock-routes";
import { ProposalCard } from "./proposal-card";
import { ACTION_LABEL, ACTION_TONE, isKnownStatus, planSummary, STATUS_LABEL } from "./proposal-math";
import "./proposals.css";

const PAGE = 10;
const code = (symbol: string) => symbol.replace(/\.TWO?$/, "");

/** One compact history row. */
export function ProposalHistoryRow({ proposal }: { proposal: Proposal }) {
  const action = typeof proposal.action === "string" ? proposal.action : "";
  return (
    <li>
      <span className={`chip tone-${ACTION_TONE[action] ?? "flat"}`}>{ACTION_LABEL[action] ?? "提案"}</span>
      <Link href={stockHref(proposal.symbol)} className="prop-stock">
        <strong>{proposal.name || code(proposal.symbol)}</strong> <span className="num">{code(proposal.symbol)}</span>
      </Link>
      <span className="prop-h-date num">{formatDateTime(proposal.created_at)}</span>
      <span className="prop-h-plan num">
        {isKnownStatus(proposal.status) ? STATUS_LABEL[proposal.status] : "—"} · {planSummary(proposal)}
      </span>
    </li>
  );
}

/**
 * 「AI 提案」: pending proposals as cards first, then a compact paged history. It sits between
 * the positions and the orders, because a proposal is the step between what you hold and the
 * order you place.
 */
export function ProposalsPanel({
  accountId,
  refreshToken,
  onUse,
}: {
  accountId: string;
  refreshToken: number;
  onUse: (proposal: Proposal) => void;
}) {
  const pending = usePolled(`prop-pending|${accountId}`, async (signal) =>
    (await listProposals(accountId, { status: "pending", limit: 50, offset: 0 }, { signal })).items,
  );
  const history = usePagedList(
    `prop-history|${accountId}`,
    (offset, limit, signal) => listProposals(accountId, { limit, offset }, { signal }),
    PAGE,
  );
  const { reload: reloadPending } = pending;
  const { reload: reloadHistory } = history;
  const first = useRef(refreshToken);
  useEffect(() => {
    if (refreshToken === first.current) return;
    reloadPending();
    reloadHistory();
  }, [refreshToken, reloadPending, reloadHistory]);

  const refresh = () => {
    reloadPending();
    reloadHistory();
  };
  const cards = pending.data ?? [];
  const past = history.items.filter((item) => item.status !== "pending");
  const error = pending.error ?? history.error;
  const loading = pending.loading && !pending.data;

  return (
    <Card title="AI 提案" aside={<SimBadge />}className="paper-proposals">
      {error && <ErrorBanner message={error.message} onDismiss={() => {}} onRetry={refresh} />}
      {loading ? (
        <span className="skeleton block" style={{ height: "6rem" }} aria-busy="true" />
      ) : cards.length > 0 ? (
        <div className="prop-section-cards" aria-live="polite">
          {cards.map((proposal) => (
            <ProposalCard key={proposal.id} proposal={proposal} onUse={onUse} onChange={refresh} />
          ))}
        </div>
      ) : (
        !error && (
          <p className="paper-empty">
            目前沒有待確認的 AI 提案。到個股頁按「產生交易提案」，AI 會依證據提出模擬交易計畫，系統檢查後會出現在這裡，由你決定要不要帶入下單。{" "}
            <Link href="/screener">去選股</Link>
          </p>
        )
      )}
      {past.length > 0 && (
        <>
          <h3 className="prop-title" style={{ marginTop: "var(--space-3)" }}>
            歷史提案
          </h3>
          <ul className="prop-history">
            {past.map((proposal) => (
              <ProposalHistoryRow key={proposal.id} proposal={proposal} />
            ))}
          </ul>
        </>
      )}
      {history.hasMore && (
        <div className="paper-more">
          <button type="button" className="button" onClick={history.loadMore} disabled={history.loadingMore}>
            {history.loadingMore ? "載入中…" : "載入更多"}
          </button>
        </div>
      )}
    </Card>
  );
}
