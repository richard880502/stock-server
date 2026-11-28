"use client";

import { useEffect, useRef, useState } from "react";
import { isAbortError } from "@/lib/api/errors";
import type { AccountSummary } from "@/lib/api/paper";
import { getProposal, type Proposal } from "@/lib/api/proposals";

export type ProposalLookup =
  | { id: string; phase: "found"; proposal: Proposal }
  | { id: string; phase: "missing" | "error" };

/**
 * Find the proposal named by `?proposal=<id>`. Proposals are fetched per account, so try the
 * selected account first, then the user's others; the caller switches to the account that
 * owns it. Null while loading or when there is no id.
 */
export function useProposalLookup(
  proposalId: string | null,
  accounts: AccountSummary[] | null,
  selectedId: string | null,
  onFound?: (proposal: Proposal) => void,
): ProposalLookup | null {
  const [result, setResult] = useState<ProposalLookup | null>(null);
  const selected = useRef(selectedId);
  const found = useRef(onFound);
  useEffect(() => {
    selected.current = selectedId;
    found.current = onFound;
  });
  const ids = accounts?.map((item) => item.id).join(",") ?? "";

  useEffect(() => {
    if (!proposalId || !ids) return;
    const controller = new AbortController();
    const order = [selected.current, ...ids.split(",")].filter(
      (id, index, all): id is string => !!id && all.indexOf(id) === index,
    );
    (async () => {
      let failed = false;
      for (const accountId of order) {
        try {
          const proposal = await getProposal(accountId, proposalId, { signal: controller.signal });
          const owned = { ...proposal, account_id: proposal.account_id ?? accountId };
          setResult({ id: proposalId, phase: "found", proposal: owned });
          found.current?.(owned);
          return;
        } catch (error) {
          if (controller.signal.aborted || isAbortError(error)) return;
          const status = (error as { status?: number }).status;
          if (status !== 404) failed = true;
        }
      }
      setResult({ id: proposalId, phase: failed ? "error" : "missing" });
    })();
    return () => controller.abort();
  }, [proposalId, ids]);

  return result && result.id === proposalId ? result : null;
}
