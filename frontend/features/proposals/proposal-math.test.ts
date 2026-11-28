import { describe, expect, it } from "vitest";
import {
  acceptBlockedReason,
  chooseAccount,
  formatDistance,
  formatRatioPct,
  groupChecks,
  keepProposalLink,
  latestPendingFor,
  parseProposalParam,
  pctFrom,
  planSummary,
  prefillFromProposal,
  proposalHref,
  proposalIsFor,
} from "./proposal-math";
import {
  PROPOSAL_BLOCKED,
  PROPOSAL_BUY,
  PROPOSAL_EXPIRED,
  PROPOSAL_HOLD,
  PROPOSAL_SPARSE,
} from "./fixtures";

describe("pctFrom / formatDistance", () => {
  it("measures the distance from the reference price in percent", () => {
    expect(pctFrom(2090, 2180)).toBeCloseTo(-4.128, 2);
    expect(pctFrom(2320, 2180)).toBeCloseTo(6.422, 2);
    expect(pctFrom(100, 100)).toBe(0);
  });
  it("is null when either side is missing or the reference is not positive", () => {
    expect(pctFrom(null, 2180)).toBeNull();
    expect(pctFrom(2090, undefined)).toBeNull();
    expect(pctFrom(2090, 0)).toBeNull();
    expect(pctFrom(Number.NaN, 10)).toBeNull();
  });
  it("prints a sign with a real minus", () => {
    expect(formatDistance(6.42)).toBe("+6.4%");
    expect(formatDistance(-4.13)).toBe("−4.1%");
    expect(formatDistance(null)).toBe("—");
    expect(formatRatioPct(0.085)).toBe("8.5%");
    expect(formatRatioPct(null)).toBe("—");
  });
});

describe("groupChecks", () => {
  it("puts blocked first, adjusted next, info last, keeping order inside a level", () => {
    const groups = groupChecks([
      { code: "a", level: "info" },
      { code: "b", level: "adjusted" },
      { code: "c", level: "blocked" },
      { code: "d", level: "adjusted" },
      { code: "e", level: "something-new" },
    ]);
    expect(groups.blocked.map((c) => c.code)).toEqual(["c"]);
    expect(groups.adjusted.map((c) => c.code)).toEqual(["b", "d"]);
    expect(groups.info.map((c) => c.code)).toEqual(["a", "e"]);
  });
  it("tolerates a missing list", () => {
    expect(groupChecks(undefined)).toEqual({ blocked: [], adjusted: [], info: [] });
    expect(groupChecks(null).blocked).toEqual([]);
  });
});

describe("acceptBlockedReason", () => {
  it("lets an order through, explains a blocked one and a missing one", () => {
    expect(acceptBlockedReason(PROPOSAL_BUY)).toBeNull();
    expect(acceptBlockedReason(PROPOSAL_BLOCKED)).toContain("系統檢查");
    expect(acceptBlockedReason(PROPOSAL_HOLD)).toContain("沒有可帶入");
    expect(acceptBlockedReason(PROPOSAL_SPARSE)).toContain("沒有可帶入");
  });
});

describe("account choice", () => {
  const accounts = [{ id: "a" }, { id: "b" }];
  it("uses the stored account while it exists, else the first", () => {
    expect(chooseAccount(accounts, "b")?.id).toBe("b");
    expect(chooseAccount(accounts, "gone")?.id).toBe("a");
    expect(chooseAccount(accounts, null)?.id).toBe("a");
  });
  it("is null without accounts", () => {
    expect(chooseAccount([], "a")).toBeNull();
    expect(chooseAccount(null, "a")).toBeNull();
  });
});

describe("symbol guards", () => {
  it("matches symbols case-insensitively and never across stocks", () => {
    expect(proposalIsFor(PROPOSAL_BUY, "2330.tw")).toBe(true);
    expect(proposalIsFor(PROPOSAL_BUY, "2317.TW")).toBe(false);
    expect(proposalIsFor(null, "2330.TW")).toBe(false);
  });
  it("finds the newest pending proposal of the stock only", () => {
    const list = [PROPOSAL_EXPIRED, { ...PROPOSAL_BUY, id: "other", symbol: "2317.TW" }, PROPOSAL_BUY];
    expect(latestPendingFor(list, "2330.TW")?.id).toBe("p-buy");
    expect(latestPendingFor(list, "2454.TW")).toBeNull();
    expect(latestPendingFor(undefined, "2330.TW")).toBeNull();
  });
});

describe("url param and prefill", () => {
  it("reads and builds the proposal param", () => {
    expect(parseProposalParam(new URLSearchParams("proposal=abc"))).toBe("abc");
    expect(parseProposalParam(new URLSearchParams("proposal="))).toBeNull();
    expect(parseProposalParam(new URLSearchParams("symbol=2330.TW"))).toBeNull();
    expect(proposalHref("a b")).toBe("/paper?proposal=a%20b");
  });
  it("fills the ticket from the proposal's order, and from nothing else", () => {
    expect(prefillFromProposal(PROPOSAL_BUY)).toEqual({
      symbol: "2330.TW",
      name: "台積電",
      side: "buy",
      lots: 1,
      limitPrice: 2175,
      proposalId: "p-buy",
    });
    expect(prefillFromProposal(PROPOSAL_BLOCKED)).toBeNull();
    expect(prefillFromProposal(PROPOSAL_SPARSE)).toBeNull();
  });
});

describe("keepProposalLink", () => {
  const link = { id: "p-buy", symbol: "2330.TW", side: "buy" as const };
  it("keeps the link while symbol and side still match", () => {
    expect(keepProposalLink(link, { symbol: "2330.tw", side: "buy" })).toBe(link);
  });
  it("drops it when the symbol changes", () => {
    expect(keepProposalLink(link, { symbol: "2317.TW", side: "buy" })).toBeNull();
    expect(keepProposalLink(link, { symbol: null, side: "buy" })).toBeNull();
  });
  it("drops it when the side flips", () => {
    expect(keepProposalLink(link, { symbol: "2330.TW", side: "sell" })).toBeNull();
  });
  it("stays null without a link", () => {
    expect(keepProposalLink(null, { symbol: "2330.TW", side: "buy" })).toBeNull();
  });
});

describe("planSummary", () => {
  it("lists what is known and falls back to a dash", () => {
    expect(planSummary(PROPOSAL_BUY)).toBe("限價 2,175 · 1 張 · 停損 2,090 · 停利 2,320");
    expect(planSummary(PROPOSAL_SPARSE)).toBe("—");
  });
});
