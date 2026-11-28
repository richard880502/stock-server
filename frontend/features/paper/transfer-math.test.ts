import { describe, expect, it } from "vitest";
import {
  cashAfterTransfer,
  checkTransfer,
  flowLabel,
  flowMarks,
  newRequestId,
  parseAmount,
  requestIdFor,
  parseInitialCash,
  resetInitialCash,
  transferAmountText,
} from "./transfer-math";

const base = { direction: "in" as const, amountText: "", note: "", cash: 800_000, availableCash: 700_000 };

describe("parseAmount", () => {
  it("accepts whole numbers with thousands separators only", () => {
    expect(parseAmount("1,000,000")).toBe(1_000_000);
    expect(parseAmount(" 5000 ")).toBe(5_000);
    expect(parseAmount("")).toBeNull();
    expect(parseAmount("12.5")).toBeNull();
    expect(parseAmount("-5")).toBeNull();
    expect(parseAmount("1e6")).toBeNull();
  });
});

describe("cash after a transfer", () => {
  it("adds a deposit and subtracts a withdrawal", () => {
    expect(cashAfterTransfer(800_000, "in", 200_000)).toBe(1_000_000);
    expect(cashAfterTransfer(800_000, "out", 200_000)).toBe(600_000);
  });
});

describe("checkTransfer", () => {
  it("is quiet and not ready while the amount is empty", () => {
    expect(checkTransfer(base)).toEqual({ amount: null, cashAfter: null, error: null, ok: false });
  });

  it("accepts the bounds of the amount range", () => {
    expect(checkTransfer({ ...base, amountText: "1000" }).ok).toBe(true);
    expect(checkTransfer({ ...base, amountText: "100,000,000" }).ok).toBe(true);
  });

  it("explains an amount outside 1,000 … 100,000,000 and unparseable text", () => {
    for (const amountText of ["999", "100000001", "0"]) {
      const check = checkTransfer({ ...base, amountText });
      expect(check.ok).toBe(false);
      expect(check.error).toContain("金額必須介於");
    }
    expect(checkTransfer({ ...base, amountText: "12.5" }).error).toContain("請輸入");
  });

  it("limits a withdrawal to the available cash, not the cash", () => {
    const over = checkTransfer({ ...base, direction: "out", amountText: "700,001" });
    expect(over.ok).toBe(false);
    expect(over.error).toContain("可用資金 NT$ 700,000");
    expect(over.cashAfter).toBeNull(); // shown as 「—」, never NT$ -… or a misleading balance
    const huge = checkTransfer({ ...base, direction: "out", amountText: "4,500,000" });
    expect(huge.cashAfter).toBeNull();
    expect(checkTransfer({ ...base, direction: "out", amountText: "999" }).cashAfter).toBeNull();
    const exact = checkTransfer({ ...base, direction: "out", amountText: "700000" });
    expect(exact.ok).toBe(true);
    expect(exact.cashAfter).toBe(100_000);
  });

  it("limits a deposit so the cash stays at or under 1,000,000,000", () => {
    const rich = { ...base, cash: 950_000_000, availableCash: 950_000_000 };
    const over = checkTransfer({ ...rich, amountText: "50,000,001" });
    expect(over.ok).toBe(false);
    expect(over.error).toContain("1,000,000,000");
    expect(over.cashAfter).toBeNull();
    expect(checkTransfer({ ...rich, amountText: "50000000" })).toMatchObject({ ok: true, cashAfter: 1_000_000_000 });
  });

  it("allows at most 60 characters of note", () => {
    expect(checkTransfer({ ...base, amountText: "5000", note: "x".repeat(60) }).ok).toBe(true);
    const long = checkTransfer({ ...base, amountText: "5000", note: "x".repeat(61) });
    expect(long.ok).toBe(false);
    expect(long.error).toContain("60");
    expect(long.cashAfter).toBe(805_000); // a note problem does not hide the preview
  });
});

describe("labels", () => {
  it("names a day's net transfer, nothing when there was none", () => {
    expect(flowLabel(1_000_000)).toBe("當日入金 NT$1,000,000");
    expect(flowLabel(-250_000)).toBe("當日出金 NT$250,000");
    expect(flowLabel(0)).toBeNull();
  });

  it("marks only the chart days that carry a transfer", () => {
    const dates = ["2026-11-24", "2026-11-25", "2026-11-26"];
    expect(flowMarks(dates, [0, 1_000_000, -50_000])).toEqual([
      { index: 1, date: "2026-11-25", label: "當日入金 NT$1,000,000" },
      { index: 2, date: "2026-11-26", label: "當日出金 NT$50,000" },
    ]);
    expect(flowMarks(dates, [0, 0, 0])).toEqual([]);
  });

  it("shows the amount without a sign or a P&L tone", () => {
    expect(transferAmountText({ amount: 200_000 })).toBe("NT$200,000");
  });

  it("makes a uuid-shaped, different request id each time", () => {
    const a = newRequestId();
    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(newRequestId()).not.toBe(a);
  });
});

describe("reset amount", () => {
  it("parses the same range as account creation", () => {
    expect(parseInitialCash("100,000")).toBe(100_000);
    expect(parseInitialCash("99999")).toBeNull();
    expect(parseInitialCash("100000001")).toBeNull();
  });

  it("is sent only when it changed", () => {
    expect(resetInitialCash("1000000", 1_000_000)).toBeUndefined();
    expect(resetInitialCash("2,000,000", 1_000_000)).toBe(2_000_000);
    expect(resetInitialCash("junk", 1_000_000)).toBeUndefined();
  });
});

describe("requestIdFor", () => {
  const counter = () => {
    let n = 0;
    return () => `id-${++n}`;
  };

  it("reuses the id for an unchanged retry and mints a new one when direction or amount change", () => {
    const make = counter();
    const first = requestIdFor({}, "in", 100_000, make);
    expect(first.id).toBe("id-1");
    // the response was lost; the same body is retried: same id, the server replays it
    const retry = requestIdFor(first.ids, "in", 100_000, make);
    expect(retry.id).toBe("id-1");
    // edited to 500,000: a new id, so it is booked instead of answered with the 100,000
    const edited = requestIdFor(retry.ids, "in", 500_000, make);
    expect(edited.id).toBe("id-2");
    const flipped = requestIdFor(edited.ids, "out", 500_000, make);
    expect(flipped.id).toBe("id-3");
    // back to the first body: its own id again, never a second booking of the 100,000
    expect(requestIdFor(flipped.ids, "in", 100_000, make).id).toBe("id-1");
  });

  it("does not mutate the ids it was given", () => {
    const ids = {};
    requestIdFor(ids, "in", 1_000, counter());
    expect(ids).toEqual({});
  });

  it("marks a post-close transfer on the live point so the jump is explained", () => {
    const marks = flowMarks(["2026-11-26", "2026-11-27"], [0, 0], [0, 1_000_000]);
    expect(marks).toEqual([{ index: 1, date: "2026-11-27", label: "收盤後入金 NT$1,000,000" }]);
    expect(flowMarks(["2026-11-27"], [-200_000], [500_000])[0].label).toBe("當日出金 NT$200,000；收盤後入金 NT$500,000");
    expect(flowLabel(-300_000, "post-close")).toBe("收盤後出金 NT$300,000");
  });
});
