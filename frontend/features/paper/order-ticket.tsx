"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { SegmentedControl } from "@/components/segmented-control";
import { SymbolCombobox } from "@/components/symbol-combobox";
import { Direction } from "@/components/direction";
import { isAbortError } from "@/lib/api/errors";
import {
  type AccountSummary,
  type Order,
  type OrderPreview,
  type OrderSide,
  type OrderType,
  type PaperRules,
  type Position,
  placeOrder,
  previewOrder,
} from "@/lib/api/paper";
import { formatPrice, formatSignedPct } from "@/lib/format";
import type { LiveQuote } from "@/lib/live-quotes";
import type { RecentSymbol } from "@/lib/recent-symbols";
import { ConfirmPanel, EstimateBlock, RULE_LINE, RulesDialog } from "./order-estimate";
import {
  buildOrderBody,
  formatPriceInput,
  isOnTickGrid,
  maxBuyLots,
  maxSellLots,
  parseLots,
  parsePrice,
  SIDE_LABEL,
  snapToTick,
  stepPrice,
  tickBandsFrom,
} from "./paper-math";
import { SimBadge } from "./paper-ui";

export type TicketPrefill = {
  /** Changes on every prefill request so the same stock can be requested twice. */
  nonce: number;
  symbol: string;
  name?: string;
  side?: OrderSide;
  lots?: number;
};

type PreviewState = { key: string; data: OrderPreview | null; error: string | null };

const PREVIEW_DEBOUNCE_MS = 250;
const PREVIEW_REFRESH_MS = 8000;

const SIDE_OPTIONS: { value: OrderSide; label: string }[] = [
  { value: "buy", label: "買進" },
  { value: "sell", label: "賣出" },
];
const TYPE_OPTIONS: { value: OrderType; label: string }[] = [
  { value: "limit", label: "限價" },
  { value: "market", label: "市價" },
];

/**
 * The order ticket. Edit step → live preview (debounced, aborted when stale) → confirm step
 * → submit. Nothing here ever talks to a broker: see the 「模擬」 badge and disclaimer.
 */
export function OrderTicket({
  account,
  positions,
  rules,
  rulesLoading,
  rulesError,
  prefill,
  quote,
  recents,
  onSymbolChange,
  onPlaced,
}: {
  account: AccountSummary;
  positions: Position[];
  rules: PaperRules | null;
  rulesLoading: boolean;
  rulesError: string | null;
  prefill: TicketPrefill | null;
  /** Live quote for the ticket's symbol (the page owns the stream). */
  quote: LiveQuote | undefined;
  recents: RecentSymbol[];
  onSymbolChange: (symbol: string | null) => void;
  onPlaced: (order: Order) => void;
}) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [name, setName] = useState<string | undefined>();
  const [side, setSide] = useState<OrderSide>("buy");
  const [orderType, setOrderType] = useState<OrderType>("limit");
  const [priceEdit, setPriceEdit] = useState<string | null>(null);
  const [lotsText, setLotsText] = useState("1");
  const [step, setStep] = useState<"edit" | "confirm">("edit");
  const [frozen, setFrozen] = useState<OrderPreview | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<{ id: string; text: string; tone: "ok" | "warn" } | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [appliedNonce, setAppliedNonce] = useState<number | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [tick, setTick] = useState(0);
  const lotsId = useId();
  const priceId = useId();

  const bands = useMemo(() => tickBandsFrom(rules), [rules]);

  // A prefill (position row, deep link) replaces the ticket's inputs once per nonce.
  if (prefill && prefill.nonce !== appliedNonce) {
    setAppliedNonce(prefill.nonce);
    setSymbol(prefill.symbol);
    setName(prefill.name);
    if (prefill.side) setSide(prefill.side);
    setLotsText(String(prefill.lots ?? 1));
    setPriceEdit(null);
    setStep("edit");
    setFrozen(null);
    setSubmitError(null);
    setPlaced(null);
  }

  useEffect(() => onSymbolChange(symbol), [symbol, onSymbolChange]);

  // Info for the chosen stock survives edits: last price, limits, tick.
  const info = preview?.data && preview.data.symbol === symbol ? preview.data : null;
  const lastPrice = quote?.price ?? info?.last_price ?? null;
  // Limit price starts at the last price once it is known, then stays where the user puts it.
  if (symbol && orderType === "limit" && priceEdit === null && lastPrice !== null && step === "edit") {
    setPriceEdit(formatPriceInput(snapToTick(lastPrice, bands)));
  }
  const priceText = priceEdit ?? "";
  const price = parsePrice(priceText);
  const priceOffGrid = orderType === "limit" && price !== null && !isOnTickGrid(price, bands);

  const body = useMemo(
    () => buildOrderBody({ symbol, side, orderType, priceText, lotsText }),
    [symbol, side, orderType, priceText, lotsText],
  );
  // While the limit price is unknown (no live quote yet) ask for a market preview just to learn
  // the last price and the day's limits; it is never shown as an estimate or submittable.
  const probe = useMemo(
    () => buildOrderBody({ symbol, side, orderType: "market", priceText: "", lotsText }),
    [symbol, side, lotsText],
  );
  const real = body && !priceOffGrid ? body : null;
  const fetchBody = real ?? probe;
  const isProbe = real === null;
  const bodyKey = fetchBody ? `${account.id}|${JSON.stringify(fetchBody)}` : null;

  useEffect(() => {
    if (bodyKey === null || !fetchBody || step === "confirm") return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      previewOrder(account.id, fetchBody, { signal: controller.signal })
        .then((data) => setPreview({ key: bodyKey, data, error: null }))
        .catch((error: unknown) => {
          if (controller.signal.aborted || isAbortError(error)) return;
          setPreview((previous) => ({
            key: bodyKey,
            data: previous?.data ?? null,
            error: error instanceof Error ? error.message : "試算失敗",
          }));
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // `fetchBody` is fully described by bodyKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bodyKey, account.id, step, tick]);

  // Keep the estimate fresh while the market moves (not while the confirm step is frozen).
  useEffect(() => {
    if (step === "confirm" || bodyKey === null) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setTick((n) => n + 1);
    }, PREVIEW_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [step, bodyKey]);

  const matched = preview && preview.key === bodyKey ? preview : null;
  const current = isProbe ? null : matched;
  const shownPreview = isProbe
    ? null
    : (current?.data ?? (preview?.data && preview.data.symbol === symbol ? preview.data : null));
  const stalePreview = !isProbe && current === null && shownPreview !== null;
  const previewLoading = !isProbe && bodyKey !== null && current === null;

  const position = positions.find((p) => p.symbol === symbol) ?? null;
  const lots = parseLots(lotsText);
  const maxLots =
    side === "sell"
      ? maxSellLots(position)
      : maxBuyLots({
          availableCash: account.available_cash,
          // No daily limit (most ETFs): the server reserves last x 1.10 for a market buy.
          price: orderType === "limit" ? (price ?? 0) : (info?.limit_up ?? (lastPrice ?? 0) * 1.1),
          discount: account.commission_discount,
          rules,
        });

  const changePct =
    quote?.change_pct != null
      ? quote.change_pct
      : info?.reference_price && info.last_price
        ? (info.last_price - info.reference_price) / info.reference_price
        : null;

  const setPrice = (next: number) => setPriceEdit(formatPriceInput(next));
  const stepBy = (direction: 1 | -1) => {
    const from = price ?? lastPrice;
    if (from === null) return;
    setPrice(stepPrice(from, direction, bands, { down: info?.limit_down, up: info?.limit_up }));
  };
  const stepLots = (delta: number) => setLotsText(String(Math.max(1, (lots ?? 0) + delta)));

  const canPreview = !!body && !priceOffGrid && !!current?.data?.ok && !previewLoading;
  const resetResult = () => {
    setPlaced(null);
    setSubmitError(null);
  };

  const pickSymbol = (next: string, nextName?: string) => {
    setSymbol(next);
    setName(nextName);
    setPriceEdit(null);
    setPreview(null);
    resetResult();
  };

  const submit = async () => {
    if (!body || !frozen) return;
    // The reviewed price, not whatever the field shows now, is what gets sent.
    setSubmitting(true);
    setSubmitError(null);
    try {
      const order = await placeOrder(account.id, body);
      onPlaced(order);
      if (order.status === "rejected") {
        setStep("edit");
        setFrozen(null);
        setPlaced({ id: order.id, text: `委託被拒絕：${order.reject_reason ?? "原因不明"}`, tone: "warn" });
        return;
      }
      const label = `${SIDE_LABEL[order.side]} ${order.name} ${order.lots} 張`;
      setPlaced({
        id: order.id,
        text:
          order.status === "filled" && order.fill
            ? `已成交：${label}，成交價 ${formatPrice(order.fill.price)}（模擬）`
            : `委託已送出：${label}，${order.order_type === "market" ? "市價" : `限價 ${formatPrice(order.limit_price ?? 0)}`}，等待撮合（模擬）`,
        tone: "ok",
      });
      setStep("edit");
      setFrozen(null);
      setPreview(null);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "送出失敗");
    } finally {
      setSubmitting(false);
    }
  };

  const confirmReady = canPreview && !!current?.data;
  const sideLabel = SIDE_LABEL[side];

  return (
    <section className={`paper-ticket card side-${side}`} aria-label="下單">
      <header className="paper-ticket-head">
        <h2 className="card-title">下單</h2>
        <SimBadge />
        <p className="paper-ticket-account">{account.name}</p>
      </header>

      {step === "confirm" && frozen && body ? (
        <ConfirmPanel
          preview={frozen}
          body={body}
          accountName={account.name}
          submitting={submitting}
          error={submitError}
          onBack={() => {
            setStep("edit");
            setFrozen(null);
            setSubmitError(null);
          }}
          onConfirm={submit}
        />
      ) : (
        <>
          <div className="paper-field">
            <SymbolCombobox
              value={symbol ?? ""}
              recents={recents}
              onSelect={pickSymbol}
              label="股票"
              placeholder="代號或名稱，例如 2330、台積電"
            />
          </div>

          {symbol && (
            <div className="paper-quote">
              <div className="paper-quote-line">
                <strong className="paper-quote-name">{info?.name ?? name ?? symbol.replace(/\.TWO?$/, "")}</strong>
                <span className="paper-quote-code num">{symbol.replace(/\.TWO?$/, "")}</span>
              </div>
              <div className="paper-quote-line">
                <span className="paper-quote-price num" aria-label="最新價">
                  {lastPrice === null ? "—" : formatPrice(lastPrice)}
                </span>
                {changePct !== null && (
                  <Direction value={changePct}>{formatSignedPct(changePct, 2)}</Direction>
                )}
                {info?.price_delayed && <small className="paper-delay">延遲</small>}
              </div>
              {info && (info.limit_up !== null || info.limit_down !== null) && (
                <div className="paper-limits" role="group" aria-label="今日漲跌停價（點選設為限價）">
                  {info.limit_up !== null && (
                    <button
                      type="button"
                      className="paper-chip up"
                      onClick={() => {
                        setOrderType("limit");
                        setPrice(info.limit_up as number);
                      }}
                    >
                      漲停 {formatPrice(info.limit_up)}
                    </button>
                  )}
                  {info.limit_down !== null && (
                    <button
                      type="button"
                      className="paper-chip down"
                      onClick={() => {
                        setOrderType("limit");
                        setPrice(info.limit_down as number);
                      }}
                    >
                      跌停 {formatPrice(info.limit_down)}
                    </button>
                  )}
                </div>
              )}
              {position && (
                <p className="paper-held">
                  已持有 {position.lots} 張
                  {position.available_shares !== position.shares &&
                    `（可賣 ${Math.floor(position.available_shares / 1000)} 張）`}
                  ，均價 {formatPrice(position.avg_cost)}
                </p>
              )}
            </div>
          )}

          <div className="paper-field">
            <SegmentedControl
              label="買賣方向"
              value={side}
              options={SIDE_OPTIONS}
              onChange={(next) => {
                setSide(next);
                resetResult();
              }}
            />
          </div>
          <div className="paper-field">
            <span className="field-label">委託類型</span>
            <SegmentedControl label="委託類型" value={orderType} options={TYPE_OPTIONS} onChange={setOrderType} />
          </div>

          {orderType === "limit" && (
            <div className="paper-field">
              <label className="field-label" htmlFor={priceId}>
                限價（元）{info?.tick ? <span className="paper-tick">每檔 {info.tick}</span> : null}
              </label>
              <div className="paper-stepper">
                <button type="button" className="icon-button" aria-label="降一檔" onClick={() => stepBy(-1)} disabled={lastPrice === null && price === null}>
                  −
                </button>
                <input
                  id={priceId}
                  className="input num"
                  inputMode="decimal"
                  value={priceText}
                  onChange={(event) => setPriceEdit(event.target.value.replace(/[^\d.]/g, ""))}
                  onBlur={() => {
                    if (price !== null && !isOnTickGrid(price, bands)) setPrice(snapToTick(price, bands));
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                      event.preventDefault();
                      stepBy(event.key === "ArrowUp" ? 1 : -1);
                    }
                  }}
                  aria-invalid={priceOffGrid || (priceText !== "" && price === null)}
                />
                <button type="button" className="icon-button" aria-label="升一檔" onClick={() => stepBy(1)} disabled={lastPrice === null && price === null}>
                  +
                </button>
              </div>
              {priceOffGrid && price !== null && (
                <p className="field-hint text-warn">
                  價格需為檔位的整數倍，離開欄位時會改為 {formatPrice(snapToTick(price, bands))}。
                </p>
              )}
            </div>
          )}

          <div className="paper-field">
            <label className="field-label" htmlFor={lotsId}>張數（1 張 ＝ 1,000 股）</label>
            <div className="paper-stepper">
              <button type="button" className="icon-button" aria-label="減少一張" onClick={() => stepLots(-1)} disabled={(lots ?? 1) <= 1}>
                −
              </button>
              <input
                id={lotsId}
                className="input num"
                inputMode="numeric"
                value={lotsText}
                onChange={(event) => setLotsText(event.target.value.replace(/\D/g, ""))}
                aria-invalid={lots === null}
              />
              <button type="button" className="icon-button" aria-label="增加一張" onClick={() => stepLots(1)}>
                +
              </button>
            </div>
            <div className="paper-quick" role="group" aria-label="張數快速選擇">
              {[1, 5, 10].map((n) => (
                <button key={n} type="button" className="paper-pill" aria-pressed={lots === n} onClick={() => setLotsText(String(n))}>
                  {n}
                </button>
              ))}
              <button
                type="button"
                className="paper-pill"
                disabled={maxLots < 1}
                aria-pressed={maxLots >= 1 && lots === maxLots}
                onClick={() => setLotsText(String(maxLots))}
              >
                {side === "buy" ? `最大可買 ${maxLots}` : `全部可賣 ${maxLots}`}
              </button>
            </div>
          </div>

          <EstimateBlock
            preview={shownPreview}
            stale={stalePreview}
            loading={previewLoading}
            error={current?.error ?? null}
            incomplete={isProbe}
          />

          <p className="paper-rule-line">
            {RULE_LINE}{" "}
            <button type="button" className="button link" onClick={() => setRulesOpen(true)}>
              撮合規則
            </button>
          </p>

          <div role="status" aria-live="polite" className="paper-placed-wrap">
            {placed && (
              <p key={placed.id} className={`paper-placed ${placed.tone}`}>
                {placed.text}
              </p>
            )}
          </div>

          <button
            type="button"
            className={`button primary block paper-submit ${side}`}
            disabled={!confirmReady || submitting}
            onClick={() => {
              if (!current?.data) return;
              setFrozen(current.data);
              setSubmitError(null);
              setPlaced(null);
              setStep("confirm");
            }}
          >
            預覽委託：{sideLabel}
            {shownPreview ? ` ${shownPreview.name}` : ""}
          </button>
          <p className="paper-disclaimer">模擬交易不會送出真實委託。</p>
        </>
      )}

      <RulesDialog
        open={rulesOpen}
        onClose={() => setRulesOpen(false)}
        rules={rules}
        loading={rulesLoading}
        error={rulesError}
        discount={account.commission_discount}
      />
    </section>
  );
}
