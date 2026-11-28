"use client";

import { type FormEvent, useId, useRef, useState } from "react";
import { SegmentedControl } from "@/components/segmented-control";
import type { AccountSummary, TransferBody, TransferDirection } from "@/lib/api/paper";
import { CASH_PICKS } from "./account-forms";
import { formatMoney, wanLabel } from "./paper-math";
import {
  checkTransfer,
  DIRECTION_LABEL,
  MAX_INITIAL_CASH,
  MAX_NOTE,
  MIN_INITIAL_CASH,
  parseInitialCash,
  type RequestIds,
  requestIdFor,
  TRANSFER_NOTE,
  TRANSFER_PICKS,
} from "./transfer-math";

const money = (value: number | null) => (value === null ? "—" : `NT$ ${formatMoney(value)}`);

const DIRECTIONS = (Object.keys(DIRECTION_LABEL) as TransferDirection[]).map((value) => ({
  value,
  label: DIRECTION_LABEL[value],
}));

/**
 * 入金／出金 dialog body. The idempotency `request_id` belongs to the (direction, amount) being
 * sent during this open of the dialog (it mounts its children only while open): a double click
 * or a retry after a dropped connection reuses it and cannot book twice, while an edited amount
 * or direction is sent under a new id (see `requestIdFor`).
 */
export function TransferForm({
  account,
  busy,
  error,
  onSubmit,
  onCancel,
  initial,
}: {
  account: Pick<AccountSummary, "name" | "cash" | "available_cash">;
  busy: boolean;
  error: string | null;
  onSubmit: (body: TransferBody) => void;
  onCancel: () => void;
  /** Starting values (tests render the states with them). */
  initial?: { direction?: TransferDirection; amountText?: string; note?: string };
}) {
  const [direction, setDirection] = useState<TransferDirection>(initial?.direction ?? "in");
  const [amountText, setAmountText] = useState(initial?.amountText ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const requestIds = useRef<RequestIds>({});
  const amountId = useId();
  const noteId = useId();
  const check = checkTransfer({
    direction,
    amountText,
    note,
    cash: account.cash,
    availableCash: account.available_cash,
  });
  const word = DIRECTION_LABEL[direction];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!check.ok || check.amount === null || busy) return;
    const trimmed = note.trim();
    const { id: requestId, ids } = requestIdFor(requestIds.current, direction, check.amount);
    requestIds.current = ids;
    onSubmit({
      direction,
      amount: check.amount,
      ...(trimmed ? { note: trimmed } : {}),
      request_id: requestId,
    });
  };

  return (
    <form className="paper-form paper-transfer-form" onSubmit={submit} noValidate>
      <div className="paper-field">
        <span className="field-label">方向</span>
        <SegmentedControl label="入金或出金" value={direction} options={DIRECTIONS} onChange={setDirection} />
      </div>
      <div className="paper-field">
        <label className="field-label" htmlFor={amountId}>{word}金額（元）</label>
        <input
          id={amountId}
          className="input num"
          inputMode="numeric"
          value={amountText}
          placeholder="例如 500000"
          autoFocus
          onChange={(event) => setAmountText(event.target.value.replace(/[^\d,]/g, ""))}
          aria-invalid={check.error !== null}
          aria-describedby={`${amountId}-hint`}
        />
        <div className="paper-quick" role="group" aria-label={`${word}金額快速選擇`}>
          {TRANSFER_PICKS.map((pick) => (
            <button
              key={pick}
              type="button"
              className="paper-pill"
              aria-pressed={check.amount === pick}
              onClick={() => setAmountText(String(pick))}
            >
              {wanLabel(pick)}
            </button>
          ))}
        </div>
        <p id={`${amountId}-hint`} className={check.error ? "field-hint text-warn" : "field-hint"} role={check.error ? "alert" : undefined}>
          {check.error ?? (check.amount !== null ? `NT$ ${formatMoney(check.amount)}` : "每筆 NT$ 1,000 到 1 億，每天最多 20 筆")}
        </p>
      </div>
      <dl className="paper-est paper-transfer-est" aria-live="polite">
        <div className="paper-est-row">
          <dt>目前現金</dt>
          <dd className="num">{money(account.cash)}</dd>
        </div>
        <div className="paper-est-row">
          <dt>可用資金</dt>
          <dd className="num">{money(account.available_cash)}</dd>
        </div>
        <div className="paper-est-row strong">
          <dt>{word}後現金</dt>
          <dd className="num">{money(check.cashAfter)}</dd>
        </div>
      </dl>
      <div className="paper-field">
        <label className="field-label" htmlFor={noteId}>備註（選填）</label>
        <input
          id={noteId}
          className="input"
          value={note}
          maxLength={MAX_NOTE + 20}
          onChange={(event) => setNote(event.target.value)}
          aria-invalid={note.trim().length > MAX_NOTE}
        />
      </div>
      <p className="paper-note paper-transfer-note">{TRANSFER_NOTE}</p>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="paper-actions">
        <button type="button" className="button" onClick={onCancel} disabled={busy}>取消</button>
        <button type="submit" className="button primary" disabled={busy || !check.ok}>
          {busy ? "處理中…" : `確認${word}`}
        </button>
      </div>
    </form>
  );
}

/** The 重設帳戶 dialog's amount field: the new initial cash (defaults to the current one). */
export function ResetAmountField({
  value,
  onChange,
  current,
}: {
  value: string;
  onChange: (value: string) => void;
  current: number;
}) {
  const id = useId();
  const parsed = parseInitialCash(value);
  const picks = CASH_PICKS.includes(current) ? CASH_PICKS : [...CASH_PICKS, current].sort((a, b) => a - b);
  return (
    <div className="paper-field">
      <label className="field-label" htmlFor={id}>重設後的初始資金（元）</label>
      <input
        id={id}
        className="input num"
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/[^\d,]/g, ""))}
        aria-invalid={parsed === null}
        aria-describedby={`${id}-hint`}
      />
      <div className="paper-quick" role="group" aria-label="初始資金快速選擇">
        {picks.map((pick) => (
          <button key={pick} type="button" className="paper-pill" aria-pressed={parsed === pick} onClick={() => onChange(String(pick))}>
            {wanLabel(pick)}
          </button>
        ))}
      </div>
      <p id={`${id}-hint`} className={parsed === null ? "field-hint text-warn" : "field-hint"}>
        {parsed === null
          ? `請輸入 ${formatMoney(MIN_INITIAL_CASH)} 到 ${formatMoney(MAX_INITIAL_CASH)} 之間的整數`
          : parsed === current
            ? `維持目前的初始資金 NT$ ${formatMoney(parsed)}`
            : `改為 NT$ ${formatMoney(parsed)}（原本 NT$ ${formatMoney(current)}）`}
      </p>
    </div>
  );
}
