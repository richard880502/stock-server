// Pure helpers for AI proposals (AI 提案): no React, no network.
import type { AccountSummary, OrderSide } from "@/lib/api/paper";
import type { CheckLevel, Proposal, ProposalCheck, ProposalStatus } from "@/lib/api/proposals";
import { ApiError } from "@/lib/api/errors";
import { formatSigned } from "@/lib/format";
import { normalizeSymbolInput } from "@/lib/symbols";

/** localStorage key the paper page keeps its chosen account under; the stock page reads it too. */
export const PAPER_ACCOUNT_KEY = "quant_signal_paper_account";

export const ACTION_LABEL: Record<string, string> = { buy: "買進", sell: "賣出", hold: "觀望" };
export const ACTION_TONE: Record<string, "up" | "down" | "flat"> = { buy: "up", sell: "down", hold: "flat" };
export const CONFIDENCE_LABEL: Record<string, string> = { low: "低", medium: "中", high: "高" };
export const STATUS_LABEL: Record<ProposalStatus, string> = {
  pending: "待確認",
  accepted: "已帶入委託",
  rejected: "未採用",
  expired: "已過期",
  superseded: "已被新提案取代",
};

export const isKnownStatus = (status: unknown): status is ProposalStatus =>
  typeof status === "string" && status in STATUS_LABEL;

/** Percent distance of `level` from `reference` (+2.3 = 2.3% above), or null when either is missing. */
export function pctFrom(level: number | null | undefined, reference: number | null | undefined): number | null {
  if (level == null || reference == null || !Number.isFinite(level) || !Number.isFinite(reference)) return null;
  if (reference <= 0) return null;
  return ((level - reference) / reference) * 100;
}

/** "+2.3%" / "−4.1%" / "0.0%"; "—" when unknown. Uses a true minus sign. */
export function formatDistance(pct: number | null): string {
  return pct === null ? "—" : `${formatSigned(pct, 1)}%`;
}

/** A ratio (0.085) as "8.5%"; "—" when unknown. */
export function formatRatioPct(ratio: number | null | undefined, digits = 1): string {
  return ratio == null || !Number.isFinite(ratio) ? "—" : `${(ratio * 100).toFixed(digits)}%`;
}

export type CheckGroups = { blocked: ProposalCheck[]; adjusted: ProposalCheck[]; info: ProposalCheck[] };

/** Blocked first, then adjusted, then info; unknown levels count as info. Order within a level is kept. */
export function groupChecks(checks: ProposalCheck[] | null | undefined): CheckGroups {
  const groups: CheckGroups = { blocked: [], adjusted: [], info: [] };
  for (const check of checks ?? []) {
    if (!check || typeof check !== "object") continue;
    const level: CheckLevel = check.level === "blocked" || check.level === "adjusted" ? check.level : "info";
    groups[level].push(check);
  }
  return groups;
}

export const hasBlocked = (proposal: Proposal) => groupChecks(proposal.checks).blocked.length > 0;

/** Why 「帶入模擬下單」 is unavailable, or null when it can be used. */
export function acceptBlockedReason(proposal: Proposal): string | null {
  if (proposal.order) return null;
  if (hasBlocked(proposal)) return "系統檢查未通過，無法照提案下單";
  return "這份提案沒有可帶入的委託";
}

/** The account the rail should use: the one the paper page stored if it still exists, else the first. */
export function chooseAccount<T extends Pick<AccountSummary, "id">>(
  accounts: T[] | null | undefined,
  storedId: string | null | undefined,
): T | null {
  const list = accounts ?? [];
  return list.find((item) => item.id === storedId) ?? list[0] ?? null;
}

const norm = (symbol: string | null | undefined) => (symbol ? (normalizeSymbolInput(symbol) ?? symbol.trim().toUpperCase()) : "");

/** A proposal belongs on a stock's page only when the symbols agree. */
export const proposalIsFor = (proposal: Pick<Proposal, "symbol"> | null | undefined, symbol: string) =>
  !!proposal && norm(proposal.symbol) === norm(symbol);

/** Newest pending proposal for `symbol` from a newest-first list. */
export function latestPendingFor(proposals: Proposal[] | null | undefined, symbol: string): Proposal | null {
  return (proposals ?? []).find((item) => item.status === "pending" && proposalIsFor(item, symbol)) ?? null;
}

export const proposalHref = (id: string) => `/paper?proposal=${encodeURIComponent(id)}`;

/** `?proposal=<id>` from the paper page's query string. */
export function parseProposalParam(params: { get(name: string): string | null } | null | undefined): string | null {
  const raw = params?.get("proposal")?.trim();
  return raw ? raw : null;
}

/**
 * The ticket remembers which proposal it was filled from, to send `proposal_id` with the order.
 * `accountId` is the proposal's own account (absent when an older API does not send it).
 */
export type ProposalLink = { id: string; symbol: string; side: OrderSide; accountId?: string | null };

/**
 * Keep the link only while the ticket still matches the proposal's symbol and side (and, when
 * both are known, its account); the moment either changes the order is no longer that
 * proposal's order.
 */
export function keepProposalLink(
  link: ProposalLink | null,
  ticket: { symbol: string | null; side: OrderSide; accountId?: string | null },
): ProposalLink | null {
  if (!link || !ticket.symbol) return null;
  if (link.accountId != null && ticket.accountId != null && link.accountId !== ticket.accountId) return null;
  return norm(ticket.symbol) === norm(link.symbol) && ticket.side === link.side ? link : null;
}

/**
 * The link after the ticket's account changed: a proposal belongs to one account, so it is
 * dropped unless the new account is known to be the proposal's own (a `?proposal=` deep link
 * switches to that account and fills the ticket at the same time).
 */
export function linkAfterAccountChange(link: ProposalLink | null, accountId: string): ProposalLink | null {
  return link && link.accountId != null && link.accountId === accountId ? link : null;
}

/** Shown when the proposal died between filling the ticket and sending the order. */
export const PROPOSAL_STALE_MESSAGE = "委託未送出：提案已失效，請確認後以一般委託送出";

/**
 * What a failed submit means for the proposal link. A 409 on an order that carried
 * `proposal_id` means the proposal is no longer pending (expired, superseded, rejected or
 * accepted elsewhere) or not this account's: the order was NOT placed, the link is dropped so
 * a retry goes out as an ordinary order — only when the user sends it again, never by itself.
 */
export function submitFailure(
  error: unknown,
  sentProposalId: string | null,
): { dropLink: boolean; message: string } {
  if (sentProposalId && error instanceof ApiError && error.status === 409) {
    return { dropLink: true, message: PROPOSAL_STALE_MESSAGE };
  }
  return { dropLink: false, message: error instanceof Error ? error.message : "送出失敗" };
}

/** The ticket prefill for a proposal, or null when it has no order to bring in. */
export function prefillFromProposal(proposal: Proposal): {
  symbol: string;
  name?: string;
  side: OrderSide;
  lots?: number;
  limitPrice?: number;
  proposalId: string;
  proposalAccountId?: string;
} | null {
  const order = proposal.order;
  if (!order) return null;
  const side = order.side === "buy" || order.side === "sell" ? order.side : null;
  if (!side) return null;
  return {
    symbol: proposal.symbol,
    name: proposal.name ?? undefined,
    side,
    lots: order.lots != null && order.lots > 0 ? order.lots : undefined,
    limitPrice: order.limit_price != null && order.limit_price > 0 ? order.limit_price : undefined,
    proposalId: proposal.id,
    ...(proposal.account_id ? { proposalAccountId: proposal.account_id } : {}),
  };
}

/** One line for the history list: "限價 2,175 · 1 張 · 停損 2,090". */
export function planSummary(proposal: Proposal): string {
  const parts: string[] = [];
  const order = proposal.order;
  if (order?.limit_price != null) parts.push(`限價 ${order.limit_price.toLocaleString("zh-TW")}`);
  if (order?.lots != null) parts.push(`${order.lots} 張`);
  if (proposal.stop_loss != null) parts.push(`停損 ${proposal.stop_loss.toLocaleString("zh-TW")}`);
  if (proposal.take_profit != null) parts.push(`停利 ${proposal.take_profit.toLocaleString("zh-TW")}`);
  return parts.length ? parts.join(" · ") : "—";
}
