// AI trade proposals (AI 提案) for paper trading. Contract: spec section "F3 contract".
// The frontend can be newer than the API, so every field except the identity ones is optional.
import { apiUrl, fetchJson, postJson } from "./client";
import { type Page, unwrapList } from "./paper";

type Init = Pick<RequestInit, "signal">;

export type ProposalStatus = "pending" | "accepted" | "rejected" | "expired" | "superseded";
export type ProposalAction = "buy" | "sell" | "hold";
export type ProposalConfidence = "low" | "medium" | "high";
export type CheckLevel = "info" | "adjusted" | "blocked";

export type ProposalOrder = {
  side?: "buy" | "sell" | null;
  order_type?: string | null;
  limit_price?: number | null;
  lots?: number | null;
  est_amount?: number | null;
};

export type ProposalCheck = { code?: string | null; level?: CheckLevel | string | null; message?: string | null };

export type ProposalSignal = {
  action?: string | null;
  score?: number | null;
  as_of?: string | null;
};

/** Ratios (0.21 = 21%), unlike the paper API's percentages. */
export type ProposalSizing = {
  equity?: number | null;
  cash_available?: number | null;
  position_lots?: number | null;
  risk_amount?: number | null;
  risk_pct?: number | null;
  position_pct?: number | null;
};

export type Proposal = {
  id: string;
  account_id?: string | null;
  symbol: string;
  name?: string | null;
  as_of?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  status?: ProposalStatus | string | null;
  action?: ProposalAction | string | null;
  order?: ProposalOrder | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  horizon_days?: number | null;
  headline?: string | null;
  rationale?: string | null;
  key_evidence?: string[] | null;
  risks?: string[] | null;
  invalidation?: string[] | null;
  evidence_refs?: string[] | null;
  confidence?: ProposalConfidence | string | null;
  signal?: ProposalSignal | null;
  aligned?: boolean | null;
  checks?: ProposalCheck[] | null;
  sizing?: ProposalSizing | null;
  reference_price?: number | null;
  model?: string | null;
  prompt_version?: string | null;
  order_id?: string | null;
  decided_at?: string | null;
  reject_reason?: string | null;
};

const base = "/api/v1/paper";
const enc = encodeURIComponent;
const root = (accountId: string) => `${base}/accounts/${enc(accountId)}/proposals`;

/** Counts as one LLM use; can take 30-60 s. */
export const createProposal = (accountId: string, symbol: string, init?: Init) =>
  postJson<Proposal>(apiUrl(root(accountId)), { symbol }, init);

export async function listProposals(
  accountId: string,
  query: { status?: ProposalStatus | null; limit: number; offset: number },
  init?: Init,
): Promise<Page<Proposal>> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  params.set("limit", String(query.limit));
  params.set("offset", String(query.offset));
  const payload = await fetchJson<unknown>(apiUrl(`${root(accountId)}?${params}`), init);
  const flag =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>).has_more
      : undefined;
  return {
    items: unwrapList<Proposal>(payload, ["proposals", "items"]),
    hasMore: typeof flag === "boolean" ? flag : null,
  };
}

export const getProposal = (accountId: string, proposalId: string, init?: Init) =>
  fetchJson<Proposal>(apiUrl(`${root(accountId)}/${enc(proposalId)}`), init);

export const rejectProposal = (accountId: string, proposalId: string, reason: string | null) =>
  postJson<Proposal>(apiUrl(`${root(accountId)}/${enc(proposalId)}/reject`), { reason });
