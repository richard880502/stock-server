// Pure helpers for 入金／出金 (deposits and withdrawals): limits, parsing, validation and
// labels. The server enforces every rule again; these only explain them before submitting.
import type { Transfer, TransferDirection } from "@/lib/api/paper";
import { formatMoney } from "./paper-math";

export const MIN_TRANSFER = 1_000;
export const MAX_TRANSFER = 100_000_000;
/** A deposit may not take the cash balance above this. */
export const MAX_CASH_BALANCE = 1_000_000_000;
export const MAX_TRANSFERS_PER_DAY = 20;
export const MAX_NOTE = 60;
export const TRANSFER_PICKS = [100_000, 500_000, 1_000_000, 5_000_000];

export const DIRECTION_LABEL: Record<TransferDirection, string> = { in: "入金", out: "出金" };

export const TRANSFER_NOTE =
  "這是模擬資金，不會動到真實的錢。入金不算獲利、出金不算虧損。報酬率以時間加權計算：收盤後的入出金算在下一個交易日。";

/** "1,000,000" -> 1000000; null unless the text is a whole number of digits (commas allowed). */
export function parseAmount(text: string): number | null {
  const cleaned = text.replace(/,/g, "").trim();
  if (!/^\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isSafeInteger(value) ? value : null;
}

/** Cash after the transfer (the signed amount added to cash). */
export const cashAfterTransfer = (cash: number, direction: TransferDirection, amount: number) =>
  direction === "in" ? cash + amount : cash - amount;

export type TransferCheck = {
  /** The amount when the text parsed to a whole number, else null. */
  amount: number | null;
  /** Cash after the transfer; null unless the amount is valid for the direction (a preview of
   * an impossible withdrawal would show a negative balance). */
  cashAfter: number | null;
  /** First broken rule in words, or null (also null while the amount is still empty). */
  error: string | null;
  /** Ready to submit. */
  ok: boolean;
};

/** Explains the rules the server enforces: range, available cash, the cash ceiling, the note. */
export function checkTransfer(input: {
  direction: TransferDirection;
  amountText: string;
  note: string;
  cash: number;
  availableCash: number;
}): TransferCheck {
  const { direction, amountText, note, cash, availableCash } = input;
  const amount = parseAmount(amountText);
  const empty = amountText.replace(/,/g, "").trim() === "";
  const noteError = note.trim().length > MAX_NOTE ? `備註最多 ${MAX_NOTE} 個字` : null;
  if (amount === null) {
    return {
      amount: null,
      cashAfter: null,
      error: empty ? noteError : `請輸入 ${formatMoney(MIN_TRANSFER)} 到 ${formatMoney(MAX_TRANSFER)} 之間的整數`,
      ok: false,
    };
  }
  const cashAfter = cashAfterTransfer(cash, direction, amount);
  let error: string | null;
  if (amount < MIN_TRANSFER || amount > MAX_TRANSFER) {
    error = `金額必須介於 NT$ ${formatMoney(MIN_TRANSFER)} 到 NT$ ${formatMoney(MAX_TRANSFER)} 之間`;
  } else if (direction === "out" && amount > availableCash) {
    error = `出金不能超過可用資金 NT$ ${formatMoney(Math.max(0, availableCash))}（現金扣掉未成交買單預留的資金）`;
  } else if (direction === "in" && cashAfter > MAX_CASH_BALANCE) {
    error = `入金後現金不能超過 NT$ ${formatMoney(MAX_CASH_BALANCE)}`;
  } else {
    error = noteError;
  }
  return { amount, cashAfter: error === null || error === noteError ? cashAfter : null, error, ok: error === null };
}

/** 「當日入金 NT$1,000,000」 for a day's net transfer; null when there was none. */
export function flowLabel(flow: number, when: "day" | "post-close" = "day"): string | null {
  if (!flow) return null;
  return `${when === "day" ? "當日" : "收盤後"}${flow > 0 ? "入金" : "出金"} NT$${formatMoney(Math.abs(flow))}`;
}

/** The chart days that carry a transfer: index, date and the 「當日入金 NT$…」 line. */
export function flowMarks(
  dates: readonly string[],
  flows: readonly number[],
  postFlows: readonly number[] = [],
): { index: number; date: string; label: string }[] {
  const marks: { index: number; date: string; label: string }[] = [];
  dates.forEach((date, index) => {
    // A transfer after the close already shows in that point's equity but belongs to the next
    // trading day's return: mark it too, or the line would seem to jump for no reason.
    const labels = [flowLabel(flows[index] ?? 0), flowLabel(postFlows[index] ?? 0, "post-close")].filter(
      (label): label is string => label !== null,
    );
    if (labels.length) marks.push({ index, date, label: labels.join("；") });
  });
  return marks;
}

/** Amount text for the transfers table: neutral on purpose, a transfer is not profit or loss. */
export const transferAmountText = (transfer: Pick<Transfer, "amount">) => `NT$${formatMoney(transfer.amount)}`;

/** A fresh idempotency key (falls back where `crypto.randomUUID` is missing, e.g. plain http). */
export function newRequestId(): string {
  const c = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const hex = (n: number) => Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  return `${hex(8)}-${hex(4)}-4${hex(3)}-${"89ab"[Math.floor(Math.random() * 4)]}${hex(3)}-${hex(12)}`;
}

/** The ids handed out during one dialog open, keyed by direction and amount. */
export type RequestIds = Readonly<Record<string, string>>;

/**
 * The `request_id` for submitting `direction` + `amount` (the note does not count): an unchanged
 * retry reuses its id, so a lost response cannot book twice, while a changed direction or amount
 * gets its own id (the server refuses a reused id with a different body with 409). Going back to
 * an amount already sent reuses that amount's id, so it is answered with the original booking.
 */
export function requestIdFor(
  ids: RequestIds,
  direction: TransferDirection,
  amount: number,
  make: () => string = newRequestId,
): { id: string; ids: RequestIds } {
  const key = `${direction}:${amount}`;
  const known = ids[key];
  if (known) return { id: known, ids };
  const id = make();
  return { id, ids: { ...ids, [key]: id } };
}

/** Same range as account creation (NewAccountForm / the API's initial_cash). */
export const MIN_INITIAL_CASH = 100_000;
export const MAX_INITIAL_CASH = 100_000_000;

/** Reset amount text -> NT$ when it is a whole number in range, else null. */
export function parseInitialCash(text: string): number | null {
  const value = parseAmount(text);
  return value !== null && value >= MIN_INITIAL_CASH && value <= MAX_INITIAL_CASH ? value : null;
}

/** The `initial_cash` to send with a reset: only when it differs from the account's current one. */
export function resetInitialCash(text: string, current: number): number | undefined {
  const value = parseInitialCash(text);
  return value !== null && value !== current ? value : undefined;
}

/** The rules-dialog line (mirrors the API's rules text). */
export const TRANSFER_RULE =
  "可隨時入金或出金（每筆 NT$1,000 至 1 億、每天最多 20 筆，出金不得超過可用資金）；" +
  "入金不算獲利、出金不算虧損。報酬率以時間加權計算：收盤後的入出金算在下一個交易日。";
