"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SparkIcon } from "@/components/icons";
import { SyncPendingNotice } from "@/components/sync-pending-notice";
import { usePolled } from "@/features/paper/use-paper-data";
import { LlmErrorNotice } from "@/features/llm/llm-error";
import type { LlmError } from "@/features/llm/use-llm-analysis";
import { ApiError } from "@/lib/api/errors";
import { listAccounts } from "@/lib/api/paper";
import { createProposal, listProposals, type Proposal } from "@/lib/api/proposals";
import { readStorage } from "@/lib/safe-storage";
import type { SyncWait } from "@/lib/sync-retry";
import { useSyncRetry } from "@/lib/use-sync-retry";
import { ProposalCard } from "./proposal-card";
import { chooseAccount, latestPendingFor, PAPER_ACCOUNT_KEY, proposalIsFor } from "./proposal-math";

export type RailProposalView = {
  phase: "hidden" | "no-account" | "ready";
  symbol: string;
  accountName: string | null;
  /** More than one account: name the one in use next to the button. */
  accountCount: number;
  proposal: Proposal | null;
  creating: boolean;
  wait: SyncWait | null;
  error: LlmError | null;
  /** The model is not configured or the stock's data is not ready. */
  disabled: boolean;
  onCreate: () => void;
  onCancelWait: () => void;
  onChange: (proposal: Proposal) => void;
};

const LINE = "AI 依證據提出模擬交易計畫；系統會檢查價位與部位大小，須由你確認才會成為模擬委託。按下會呼叫模型。";

/** The third action of the stock page's AI rail: ask for a paper-trade proposal. Pure view. */
export function RailProposalView(view: RailProposalView) {
  if (view.phase === "hidden") return null;
  // A proposal for another stock must never render here, whatever the state above says.
  const proposal = proposalIsFor(view.proposal, view.symbol) ? view.proposal : null;
  const pending = proposal?.status === "pending";
  return (
    <div className="prop-rail" aria-label="AI 交易提案">
      {view.phase === "no-account" ? (
        <>
          <Link href="/paper" className="button small">
            先建立模擬帳戶
          </Link>
          <p className="prop-rail-line">交易提案需要一個模擬帳戶，才能依你的現金與持股決定張數。</p>
        </>
      ) : (
        <>
          <button
            type="button"
            className="button small"
            onClick={view.onCreate}
            disabled={view.disabled || view.creating}
            title={pending ? "會再次呼叫模型，目前這份提案會被取代" : undefined}
          >
            <SparkIcon size={14} />
            {view.creating ? "產生提案中…" : pending ? "重新產生交易提案" : "產生交易提案"}
          </button>
          {view.accountCount > 1 && view.accountName && (
            <p className="prop-rail-line">使用模擬帳戶：{view.accountName}</p>
          )}
          <p className="prop-rail-line">{LINE}</p>
        </>
      )}

      {view.wait ? (
        <SyncPendingNotice wait={view.wait} onCancel={view.onCancelWait} />
      ) : view.error && !view.creating ? (
        <LlmErrorNotice error={view.error} onRetry={view.onCreate} />
      ) : view.creating ? (
        <div className="ai-block ai-skeleton" role="status" aria-label="交易提案產生中，可能需要 30 到 60 秒">
          <span className="skeleton line" style={{ width: "40%" }} />
          <span className="skeleton line" />
          <span className="skeleton line" style={{ width: "85%" }} />
          <span className="skeleton line" style={{ width: "60%" }} />
        </div>
      ) : proposal ? (
        <ProposalCard proposal={proposal} onChange={view.onChange} inRail />
      ) : null}
    </div>
  );
}

type Local = { key: string; proposal: Proposal };
type Failure = { key: string; error: LlmError };

/** State and requests behind `RailProposalView`, keyed by account and symbol. */
export function useRailProposal(symbol: string, configured: boolean): RailProposalView {
  const accounts = usePolled("rail-accounts", (signal) => listAccounts({ signal }));
  const list = accounts.data?.accounts ?? null;
  const account = chooseAccount(list, readStorage(PAPER_ACCOUNT_KEY));
  const accountId = account?.id ?? null;
  const key = accountId ? `${accountId}|${symbol.toUpperCase()}` : null;

  // The newest pending proposal for this stock, so a reload neither loses it nor pays twice.
  const pending = usePolled(
    accountId ? `rail-pending|${accountId}` : null,
    (signal) => listProposals(accountId as string, { status: "pending", limit: 50, offset: 0 }, { signal }),
  );

  const [local, setLocal] = useState<Local | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const retry = useSyncRetry();
  const { cancel } = retry;
  useEffect(() => cancel, [key, cancel]);

  const create = async () => {
    if (!accountId || !key) return;
    setFailure(null);
    try {
      const result = await retry.run((signal) => createProposal(accountId, symbol, { signal }));
      if (result.aborted) return;
      setLocal({ key, proposal: result.value });
    } catch (error) {
      setFailure({
        key,
        error: {
          message: error instanceof Error ? error.message : "產生交易提案失敗",
          status: error instanceof ApiError ? error.status : null,
        },
      });
    }
  };

  const fromList = pending.data ? latestPendingFor(pending.data.items, symbol) : null;
  const mine = local && local.key === key ? local.proposal : null;
  const proposal = mine ?? fromList;

  let phase: RailProposalView["phase"] = "hidden";
  if (accounts.error?.status === 403) phase = "hidden";
  else if (list && !account) phase = "no-account";
  else if (account) phase = "ready";

  return {
    phase,
    symbol,
    accountName: account?.name ?? null,
    accountCount: list?.length ?? 0,
    proposal,
    creating: retry.busy,
    wait: retry.wait,
    error: failure && failure.key === key ? failure.error : null,
    disabled: !configured,
    onCreate: () => void create(),
    onCancelWait: retry.cancel,
    onChange: (updated) => key && setLocal({ key, proposal: updated }),
  };
}

export function RailProposal({ symbol, configured }: { symbol: string; configured: boolean }) {
  return <RailProposalView {...useRailProposal(symbol, configured)} />;
}
