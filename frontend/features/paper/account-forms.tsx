"use client";

import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { SegmentedControl } from "@/components/segmented-control";
import type { AccountPatch, AccountSummary, NewAccount } from "@/lib/api/paper";
import { discountLabel, formatMoney, wanLabel } from "./paper-math";

export const CASH_PICKS = [500_000, 1_000_000, 3_000_000, 10_000_000];
const DISCOUNT_PICKS = [1, 0.6, 0.28];
export const MIN_CASH = 100_000;
export const MAX_CASH = 100_000_000;

function DiscountPicker({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const picks = DISCOUNT_PICKS.includes(value) ? DISCOUNT_PICKS : [...DISCOUNT_PICKS, value];
  return (
    <SegmentedControl
      label="手續費折扣"
      value={value}
      options={picks.map((pick) => ({ value: pick, label: discountLabel(pick) }))}
      onChange={onChange}
    />
  );
}

function DayTradeSwitch({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  const hintId = useId();
  return (
    <div className="paper-switch-row">
      <label className="paper-switch">
        <input
          type="checkbox"
          role="switch"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby={hintId}
        />
        <span>允許現股當沖</span>
      </label>
      <p id={hintId} className="field-hint">
        開啟後，當天買進的股票當天就能賣出，賣出時改用當沖交易稅率計算。
      </p>
    </div>
  );
}

/** Create-account form: the first-run card and the 「新增帳戶」 dialog share it. */
export function NewAccountForm({
  onSubmit,
  busy,
  error,
  submitLabel = "建立模擬帳戶",
}: {
  onSubmit: (body: NewAccount) => void;
  busy: boolean;
  error: string | null;
  submitLabel?: string;
}) {
  const [name, setName] = useState("主帳戶");
  const [cashText, setCashText] = useState("1000000");
  const [discount, setDiscount] = useState(1);
  const [dayTrade, setDayTrade] = useState(false);
  const nameId = useId();
  const cashId = useId();
  const cash = Number(cashText.replace(/,/g, ""));
  const cashValid = Number.isInteger(cash) && cash >= MIN_CASH && cash <= MAX_CASH;
  const nameValid = name.trim().length > 0 && name.trim().length <= 30;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!cashValid || !nameValid || busy) return;
    onSubmit({ name: name.trim(), initial_cash: cash, commission_discount: discount, allow_day_trade: dayTrade });
  };

  return (
    <form className="paper-form" onSubmit={submit} noValidate>
      <div className="paper-field">
        <label className="field-label" htmlFor={nameId}>帳戶名稱</label>
        <input
          id={nameId}
          className="input"
          value={name}
          maxLength={30}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={!nameValid}
        />
      </div>
      <div className="paper-field">
        <label className="field-label" htmlFor={cashId}>初始資金（元）</label>
        <input
          id={cashId}
          className="input num"
          inputMode="numeric"
          value={cashText}
          onChange={(event) => setCashText(event.target.value.replace(/[^\d,]/g, ""))}
          aria-invalid={!cashValid}
          aria-describedby={`${cashId}-hint`}
        />
        <div className="paper-quick" role="group" aria-label="初始資金快速選擇">
          {CASH_PICKS.map((pick) => (
            <button
              key={pick}
              type="button"
              className="paper-pill"
              aria-pressed={cash === pick}
              onClick={() => setCashText(String(pick))}
            >
              {wanLabel(pick)}
            </button>
          ))}
        </div>
        <p id={`${cashId}-hint`} className={cashValid ? "field-hint" : "field-hint text-warn"}>
          {cashValid
            ? `NT$ ${formatMoney(cash)}`
            : `請輸入 ${formatMoney(MIN_CASH)} 到 ${formatMoney(MAX_CASH)} 之間的整數`}
        </p>
      </div>
      <div className="paper-field">
        <span className="field-label">手續費折扣</span>
        <DiscountPicker value={discount} onChange={setDiscount} />
      </div>
      <DayTradeSwitch checked={dayTrade} onChange={setDayTrade} />
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="button primary block" disabled={busy || !cashValid || !nameValid}>
        {busy ? "建立中…" : submitLabel}
      </button>
    </form>
  );
}

export type SettingsFocus = "name" | "discount" | "daytrade";

/** 改名 / 手續費折扣 / 當沖 in one form; the menu item picks which field to focus. */
export function AccountSettingsForm({
  account,
  focus,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  account: AccountSummary;
  focus: SettingsFocus;
  busy: boolean;
  error: string | null;
  onSubmit: (patch: AccountPatch) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(account.name);
  const [discount, setDiscount] = useState(account.commission_discount);
  const [dayTrade, setDayTrade] = useState(account.allow_day_trade);
  const nameId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const target = formRef.current?.querySelector<HTMLElement>(
      focus === "discount" ? '[data-focus="discount"] [tabindex="0"]' : focus === "daytrade" ? '[data-focus="daytrade"] input' : "input",
    );
    target?.focus();
  }, [focus]);
  const nameValid = name.trim().length > 0 && name.trim().length <= 30;
  const changed =
    name.trim() !== account.name || discount !== account.commission_discount || dayTrade !== account.allow_day_trade;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!nameValid || !changed || busy) return;
    const patch: AccountPatch = {};
    if (name.trim() !== account.name) patch.name = name.trim();
    if (discount !== account.commission_discount) patch.commission_discount = discount;
    if (dayTrade !== account.allow_day_trade) patch.allow_day_trade = dayTrade;
    onSubmit(patch);
  };

  return (
    <form ref={formRef} className="paper-form" onSubmit={submit} noValidate>
      <div className="paper-field">
        <label className="field-label" htmlFor={nameId}>帳戶名稱</label>
        <input
          id={nameId}
          className="input"
          value={name}
          maxLength={30}
          autoFocus={focus === "name"}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={!nameValid}
        />
      </div>
      <div className="paper-field" data-focus="discount">
        <span className="field-label">手續費折扣</span>
        <DiscountPicker value={discount} onChange={setDiscount} />
        <p className="field-hint">只影響之後的成交，已成交的紀錄不會改變。</p>
      </div>
      <div data-focus="daytrade">
        <DayTradeSwitch checked={dayTrade} onChange={setDayTrade} />
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="paper-actions">
        <button type="button" className="button" onClick={onCancel}>取消</button>
        <button type="submit" className="button primary" disabled={busy || !nameValid || !changed}>
          {busy ? "儲存中…" : "儲存"}
        </button>
      </div>
    </form>
  );
}
