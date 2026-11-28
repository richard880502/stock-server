// Contract-shaped proposals for tests and local review (spec section "F3 contract").
import type { Proposal } from "@/lib/api/proposals";

const BASE: Proposal = {
  id: "p-buy",
  account_id: "acc-1",
  symbol: "2330.TW",
  name: "台積電",
  as_of: "2026-11-27",
  created_at: "2026-11-28T01:30:00Z",
  expires_at: "2026-11-30T05:30:00Z",
  status: "pending",
  action: "buy",
  order: { side: "buy", order_type: "limit", limit_price: 2175, lots: 1, est_amount: 2175000 },
  stop_loss: 2090,
  take_profit: 2320,
  horizon_days: 20,
  headline: "回測後拉回的買點，分批建立小部位",
  rationale: "法人連三日買超，價格回到月線附近，量縮整理。",
  key_evidence: ["外資連三日買超", "收盤守住月線"],
  risks: ["大盤轉弱時容易跟著回檔"],
  invalidation: ["收盤跌破 2090"],
  evidence_refs: ["signal", "chip_flow"],
  confidence: "medium",
  signal: { action: "increase", score: 68.2, as_of: "2026-11-27" },
  aligned: true,
  checks: [
    { code: "price_snapped", level: "adjusted", message: "限價 2177 不在檔位上，已調整為 2175。" },
    { code: "lots_capped", level: "adjusted", message: "張數由 3 張降為 1 張，使單筆虧損不超過資產的 1%。" },
    { code: "fresh_data", level: "info", message: "使用 2026-11-27 的收盤資料。" },
  ],
  sizing: {
    equity: 1000000,
    cash_available: 640000,
    position_lots: 0,
    risk_amount: 85000,
    risk_pct: 0.085,
    position_pct: 0.21,
  },
  reference_price: 2180,
  model: "claude-opus-5-5",
  prompt_version: "trade_proposal_v1",
  order_id: null,
  decided_at: null,
  reject_reason: null,
};

export const PROPOSAL_BUY: Proposal = BASE;

export const PROPOSAL_BLOCKED: Proposal = {
  ...BASE,
  id: "p-blocked",
  headline: "想加碼，但現金不夠",
  order: null,
  checks: [
    { code: "insufficient_cash", level: "blocked", message: "可用現金 NT$ 120,000 不足以買進 1 張（約需 NT$ 2,175,000）。" },
    { code: "price_snapped", level: "adjusted", message: "限價已調整為檔位。" },
    { code: "fresh_data", level: "info", message: "使用最近收盤資料。" },
  ],
  sizing: { equity: 400000, cash_available: 120000, position_lots: 0, risk_amount: null, risk_pct: null, position_pct: null },
};

export const PROPOSAL_SELL: Proposal = {
  ...BASE,
  id: "p-sell",
  action: "sell",
  headline: "獲利了結，賣出一半持股",
  order: { side: "sell", order_type: "limit", limit_price: 2190, lots: 2, est_amount: 4380000 },
  stop_loss: null,
  take_profit: null,
  signal: { action: "hold", score: 51, as_of: "2026-11-27" },
  checks: [],
  sizing: { equity: 1000000, cash_available: 640000, position_lots: 4, risk_amount: null, risk_pct: null, position_pct: null },
};

export const PROPOSAL_HOLD: Proposal = {
  ...BASE,
  id: "p-hold",
  action: "hold",
  headline: "證據分歧，先觀望",
  order: null,
  stop_loss: null,
  take_profit: null,
  horizon_days: null,
  checks: [],
};

export const PROPOSAL_MISALIGNED: Proposal = {
  ...BASE,
  id: "p-misaligned",
  headline: "逆著量化訊號的短線搶反彈",
  aligned: false,
  signal: { action: "avoid", score: 28.4, as_of: "2026-11-27" },
};

export const PROPOSAL_EXPIRED: Proposal = { ...BASE, id: "p-expired", status: "expired" };

export const PROPOSAL_ACCEPTED: Proposal = {
  ...BASE,
  id: "p-accepted",
  status: "accepted",
  order_id: "order-1",
  decided_at: "2026-11-28T02:10:00Z",
};

export const PROPOSAL_REJECTED: Proposal = {
  ...BASE,
  id: "p-rejected",
  status: "rejected",
  reject_reason: "價位太高",
  decided_at: "2026-11-28T02:10:00Z",
};

export const PROPOSAL_SUPERSEDED: Proposal = { ...BASE, id: "p-superseded", status: "superseded" };

/** Most fields missing, as when the frontend is newer than the API. */
export const PROPOSAL_SPARSE: Proposal = { id: "p-sparse", symbol: "2317.TW", status: "pending" };

export const ALL_PROPOSALS: Proposal[] = [
  PROPOSAL_BUY,
  PROPOSAL_BLOCKED,
  PROPOSAL_SELL,
  PROPOSAL_HOLD,
  PROPOSAL_MISALIGNED,
  PROPOSAL_EXPIRED,
  PROPOSAL_ACCEPTED,
  PROPOSAL_REJECTED,
  PROPOSAL_SUPERSEDED,
  PROPOSAL_SPARSE,
];
