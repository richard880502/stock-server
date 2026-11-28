import { describe, expect, it } from "vitest";
import {
  cashAfterTransfer,
  checkTransfer,
  flowLabel,
  flowMarks,
  newRequestId,
  parseAmount,
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
    expect(over.cashAfter).toBe(99_999);
    const exact = checkTransfer({ ...base, direction: "out", amountText: "700000" });
    expect(exact.ok).toBe(true);
    expect(exact.cashAfter).toBe(100_000);
  });

  it("limits a deposit so the cash stays at or under 1,000,000,000", () => {
    const rich = { ...base, cash: 950_000_000, availableCash: 950_000_000 };
    const over = checkTransfer({ ...rich, amountText: "50,000,001" });
    expect(over.ok).toBe(false);
    expect(over.error).toContain("1,000,000,000");
    expect(checkTransfer({ ...rich, amountText: "50000000" })).toMatchObject({ ok: true, cashAfter: 1_000_000_000 });
  });

  it("allows at most 60 characters of note", () => {
    expect(checkTransfer({ ...base, amountText: "5000", note: "x".repeat(60) }).ok).toBe(true);
    const long = checkTransfer({ ...base, amountText: "5000", note: "x".repeat(61) });
    expect(long.ok).toBe(false);
    expect(long.error).toContain("60");
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
