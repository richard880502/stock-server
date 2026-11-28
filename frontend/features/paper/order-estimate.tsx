"use client";

import { useEffect, useRef } from "react";
import { AlertIcon } from "@/components/icons";
import type { OrderPreview, OrderRequest, PaperRules } from "@/lib/api/paper";
import { formatPrice } from "@/lib/format";
import {
  discountLabel,
  formatMoney,
  formatSessionDate,
  SIDE_LABEL,
  tickBandsFrom,
} from "./paper-math";
import { PaperDialog, SIM_DISCLAIMER, SimBadge } from "./paper-ui";
import { TRANSFER_RULE } from "./transfer-math";

export const RULE_LINE = "市價單以下一筆成交價加一檔滑價成交；限價單在市場成交價到達你的價格時成交。整股、當日有效。";

function Row({ label, value, strong = false, muted = false }: { label: string; value: string; strong?: boolean; muted?: boolean }) {
  return (
    <div className={`paper-est-row${strong ? " strong" : ""}${muted ? " muted" : ""}`}>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

const money = (value: number | null) => (value === null ? "—" : `NT$ ${formatMoney(value)}`);

/**
 * Live cost estimate from `POST …/orders?preview=true`. `aria-live="polite"` so a screen reader
 * hears the new total (or the reject reason) when the inputs change.
 */
export function EstimateBlock({
  preview,
  stale,
  loading,
  error,
  incomplete,
}: {
  preview: OrderPreview | null;
  /** The shown preview belongs to earlier inputs: dim it. */
  stale: boolean;
  loading: boolean;
  error: string | null;
  /** Inputs are not yet enough to request a preview. */
  incomplete: boolean;
}) {
  let body;
  if (error && !preview) {
    body = (
      <p className="paper-reject" role="alert">
        <AlertIcon size={16} /> 無法試算：{error}
      </p>
    );
  } else if (!preview) {
    body = (
      <p className="paper-est-empty">
        {incomplete ? "選擇股票並輸入張數後，這裡會即時試算成交金額與費用。" : loading ? "試算中…" : ""}
      </p>
    );
  } else {
    const sell = preview.side === "sell";
    body = (
      <>
        {!preview.ok && (
          <p className="paper-reject" role="alert">
            <AlertIcon size={16} /> {preview.reject_reason ?? "此委託無法送出"}
          </p>
        )}
        {preview.will_queue_for_next_session && (
          <p className="paper-queue">
            現在非交易時段，將排入 {formatSessionDate(preview.session_date)} 開盤後撮合
          </p>
        )}
        <dl className={`paper-est${stale ? " is-refreshing" : ""}`}>
          <Row
            label={preview.order_type === "market" ? "預估成交價（含一檔滑價）" : "預估成交價"}
            value={preview.estimated_price === null ? "—" : formatPrice(preview.estimated_price)}
          />
          <Row label="預估金額" value={money(preview.estimated_amount)} />
          <Row label="手續費" value={money(preview.estimated_commission)} />
          <Row label="交易稅" value={money(preview.estimated_tax)} />
          {sell ? (
            <Row label="預估入帳" value={money(preview.estimated_total)} strong />
          ) : (
            <>
              <Row label="合計" value={money(preview.estimated_total)} strong />
              <Row label="圈存金額" value={money(preview.reserve)} muted />
            </>
          )}
          <Row label="下單後可用資金" value={money(preview.available_cash_after)} muted />
        </dl>
        {preview.warnings.length > 0 && (
          <ul className="paper-warnings">
            {preview.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
      </>
    );
  }
  return (
    <div className="paper-estimate" aria-live="polite" aria-atomic="false" aria-label="預估試算">
      {body}
    </div>
  );
}

/** Second step of the two-step submit: restate everything before 「確認送出」. */
export function ConfirmPanel({
  preview,
  body,
  accountName,
  submitting,
  error,
  onBack,
  onConfirm,
}: {
  preview: OrderPreview;
  body: OrderRequest;
  accountName: string;
  submitting: boolean;
  error: string | null;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headingRef.current?.focus(), []);
  const sell = body.side === "sell";
  const price =
    body.order_type === "limit" ? `限價 ${formatPrice(body.limit_price ?? 0)}` : "市價（含一檔滑價）";
  return (
    <div
      className="paper-confirm"
      role="group"
      aria-labelledby="paper-confirm-title"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !submitting) {
          event.stopPropagation();
          onBack();
        }
      }}
    >
      <div className="paper-confirm-head">
        <h3 id="paper-confirm-title" ref={headingRef} tabIndex={-1}>確認委託</h3>
        <SimBadge />
      </div>
      <p className={`paper-confirm-side ${body.side}`}>
        {SIDE_LABEL[body.side]} {preview.name} <span className="num">{body.symbol.replace(/\.TWO?$/, "")}</span>
      </p>
      <dl className="paper-est">
        <Row label="帳戶" value={accountName} />
        <Row label="數量" value={`${body.lots} 張（${formatMoney(body.lots * 1000)} 股）`} />
        <Row label="價格" value={price} />
        <Row label={sell ? "預估入帳" : "預估合計"} value={money(preview.estimated_total)} strong />
        {!sell && <Row label="圈存金額" value={money(preview.reserve)} muted />}
        <Row label="有效日" value={`${formatSessionDate(preview.session_date)} 當日有效`} />
      </dl>
      {preview.will_queue_for_next_session && (
        <p className="paper-queue">現在非交易時段，將排入 {formatSessionDate(preview.session_date)} 開盤後撮合</p>
      )}
      <p className="paper-disclaimer">{SIM_DISCLAIMER}。</p>
      {error && (
        <p className="paper-reject" role="alert">
          <AlertIcon size={16} /> {error}
        </p>
      )}
      <div className="paper-actions">
        <button type="button" className="button" onClick={onBack} disabled={submitting}>
          返回修改
        </button>
        <button type="button" className={`button primary paper-submit ${body.side}`} onClick={onConfirm} disabled={submitting}>
          {submitting ? "送出中…" : "確認送出"}
        </button>
      </div>
    </div>
  );
}

const pct = (value: number) => `${Number((value * 100).toFixed(4))}%`;

/** 「撮合規則」 dialog: the fixed matching copy plus whatever GET /paper/rules says. */
export function RulesDialog({
  open,
  onClose,
  rules,
  loading,
  error,
  discount,
}: {
  open: boolean;
  onClose: () => void;
  rules: PaperRules | null;
  loading: boolean;
  error: string | null;
  discount: number;
}) {
  const bands = tickBandsFrom(rules);
  const costs: [string, string][] = [];
  if (rules?.commission_rate != null)
    costs.push([
      "手續費",
      `${pct(rules.commission_rate)} × 你的折扣（${discountLabel(discount)}），每筆最低 ${rules.commission_min ?? 20} 元`,
    ]);
  if (rules?.tax_rate != null) costs.push(["證券交易稅（賣出）", pct(rules.tax_rate)]);
  if (rules?.etf_tax_rate != null) costs.push(["ETF 交易稅（賣出）", pct(rules.etf_tax_rate)]);
  if (rules?.bond_etf_tax_exempt_through)
    costs.push(["債券 ETF 交易稅（賣出）", `免徵至 ${rules.bond_etf_tax_exempt_through}，之後依 ETF 稅率`]);
  if (rules?.day_trade_tax_rate != null) costs.push(["現股當沖交易稅（賣出）", pct(rules.day_trade_tax_rate)]);
  if (rules?.price_limit_pct != null) costs.push(["漲跌幅限制", `參考價 ±${rules.price_limit_pct}%`]);
  if (rules?.slippage_ticks != null) costs.push(["滑價", `${rules.slippage_ticks} 檔`]);
  return (
    <PaperDialog open={open} onClose={onClose} title="撮合規則" wide>
      <div className="paper-rules">
        <p className="paper-disclaimer">{SIM_DISCLAIMER}。這裡只用我們自己儲存的報價撮合，不會連到任何券商。</p>
        <ul className="paper-rule-list">
          <li>交易時段：盤中 09:00–13:25 逐筆撮合，13:30 收盤試搓結果。</li>
          <li>{RULE_LINE}</li>
          <li>市價買單遇漲停鎖死、市價賣單遇跌停鎖死時不成交。</li>
          <li>收盤後或非交易日下的單，排入下一個交易日，從該日第一筆成交開始撮合。</li>
          <li>全部成交或不成交，沒有部分成交；當日未成交的委託收盤後失效。</li>
          <li>買單會先圈存「價格 × 股數 ＋ 手續費」，市價單以漲停價估算。</li>
          <li>現股當沖需在帳戶選項中開啟，否則當天買進的股票當天不能賣。</li>
          <li>{TRANSFER_RULE}</li>
          <li>債券 ETF（代號尾碼 B）賣出免徵證交稅，現行法定期限至 2026-12-31，之後自動依 ETF 稅率計算。</li>
          <li>
            ETF 的漲跌幅限制無法由代號判斷（例如 0050 實際有 ±10%，國外成分 ETF 則沒有），因此 ETF
            不檢查漲跌幅、預覽不顯示漲跌停價；撮合只用真實成交價，不會產生市場上沒有成交過的價格。
          </li>
        </ul>
        {loading && !rules && <span className="skeleton block" style={{ height: "6rem" }} />}
        {error && <p className="inline-error">無法載入最新規則：{error}</p>}
        {costs.length > 0 && (
          <>
            <h3>費用與限制</h3>
            <dl className="paper-est">
              {costs.map(([label, value]) => (
                <Row key={label} label={label} value={value} />
              ))}
            </dl>
          </>
        )}
        <h3>升降單位（檔位）</h3>
        <table className="data-table paper-table">
          <thead>
            <tr>
              <th scope="col">價格區間</th>
              <th scope="col">每檔</th>
            </tr>
          </thead>
          <tbody>
            {bands.map((band) => (
              <tr key={band.min}>
                <th scope="row">
                  {band.max === null ? `${formatPrice(band.min)} 以上` : `${formatPrice(band.min)} – ${formatPrice(band.max)} 未滿`}
                </th>
                <td className="num">{band.tick}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="paper-actions">
          <button type="button" className="button primary" onClick={onClose}>
            知道了
          </button>
        </div>
      </div>
    </PaperDialog>
  );
}
