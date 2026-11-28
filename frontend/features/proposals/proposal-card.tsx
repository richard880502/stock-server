"use client";

import Link from "next/link";
import { type ReactNode, useId, useState } from "react";
import { formatDateTime, formatMoney } from "@/features/paper/paper-math";
import { SimBadge } from "@/features/paper/paper-ui";
import { BacktestLink, AI_DISCLAIMER } from "@/features/terminal/ai-parts";
import { type Proposal, rejectProposal } from "@/lib/api/proposals";
import { actionCopy } from "@/lib/copy";
import { formatPrice } from "@/lib/format";
import { stockHref } from "@/lib/stock-routes";
import {
  ACTION_LABEL,
  ACTION_TONE,
  acceptBlockedReason,
  CONFIDENCE_LABEL,
  formatDistance,
  formatRatioPct,
  groupChecks,
  isKnownStatus,
  pctFrom,
  proposalHref,
  STATUS_LABEL,
} from "./proposal-math";
import "./proposals.css";

const code = (symbol: string) => symbol.replace(/\.TWO?$/, "");
const dash = "—";

function Cell({ label, value, hint }: { label: string; value: ReactNode; hint?: string | null }) {
  return (
    <div className="prop-cell">
      <dt>{label}</dt>
      <dd className="num">
        {value}
        {hint ? <small>{hint}</small> : null}
      </dd>
    </div>
  );
}

function LevelCell({
  label,
  level,
  reference,
}: {
  label: string;
  level: number | null | undefined;
  reference: number | null | undefined;
}) {
  if (level == null) return <Cell label={label} value={dash} />;
  const distance = pctFrom(level, reference);
  return (
    <Cell label={label} value={formatPrice(level)} hint={distance === null ? null : `${formatDistance(distance)} 對參考價`} />
  );
}

function List({ title, items }: { title: string; items: string[] | null | undefined }) {
  const list = (items ?? []).filter((item) => typeof item === "string" && item.trim());
  if (list.length === 0) return null;
  return (
    <>
      <h5 className="prop-sub">{title}</h5>
      <ul className="prop-list">
        {list.map((item, index) => (
          <li key={`${index}-${item}`}>{item}</li>
        ))}
      </ul>
    </>
  );
}

/** What happened to a proposal that is no longer pending. */
function outcomeLine(proposal: Proposal): string | null {
  const when = proposal.decided_at ? `（${formatDateTime(proposal.decided_at)}）` : "";
  switch (proposal.status) {
    case "accepted":
      return `已帶入模擬委託${when}`;
    case "rejected":
      return `未採用${proposal.reject_reason ? `：${proposal.reject_reason}` : ""}${when}`;
    case "expired":
      return "已過期，價格與部位可能已改變，需要時請重新產生提案";
    case "superseded":
      return "已被較新的提案取代";
    default:
      return null;
  }
}

/**
 * One AI proposal. Narrow enough for the stock page's 380px rail; the system checks stay
 * above the fold whenever something was blocked or adjusted, because that is what tells the
 * reader how far to trust the model's numbers. `onUse` lets the paper page fill its own
 * ticket; without it the button links to the paper page.
 */
export function ProposalCard({
  proposal,
  onUse,
  onChange,
  inRail = false,
}: {
  proposal: Proposal;
  onUse?: (proposal: Proposal) => void;
  /** Called with the updated proposal after 「不採用」 / 「收起」. */
  onChange?: (proposal: Proposal) => void;
  inRail?: boolean;
}) {
  const headingId = useId();
  const reasonId = useId();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const action = typeof proposal.action === "string" ? proposal.action : "";
  const order = proposal.order ?? null;
  const reference = proposal.reference_price;
  const checks = groupChecks(proposal.checks);
  const pending = proposal.status === "pending";
  const status = isKnownStatus(proposal.status) ? proposal.status : null;
  const outcome = outcomeLine(proposal);
  const signal = proposal.signal ?? null;
  const signalCopy = signal?.action ? (actionCopy as Record<string, { label: string }>)[signal.action] : undefined;
  const sizing = proposal.sizing ?? null;
  const hasLoss = sizing?.risk_amount != null;
  const blockedReason = acceptBlockedReason(proposal);
  const hold = action === "hold";
  const noisyChecks = checks.blocked.length + checks.adjusted.length > 0;

  const reject = async (withReason: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await rejectProposal(proposal.account_id ?? "", proposal.id, withReason);
      setRejecting(false);
      onChange?.(updated);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      id={`proposal-${proposal.id}`}
      className={`prop-card${inRail ? " in-rail" : ""}`}
      aria-labelledby={headingId}
      data-status={proposal.status ?? undefined}
    >
      <header className="prop-top">
        <span className={`chip tone-${ACTION_TONE[action] ?? "flat"}`}>{ACTION_LABEL[action] ?? "提案"}</span>
        {status && status !== "pending" && <span className="chip plain">{STATUS_LABEL[status]}</span>}
        <Link href={stockHref(proposal.symbol)} className="prop-stock" id={headingId}>
          <strong>{proposal.name || code(proposal.symbol)}</strong> <span className="num">{code(proposal.symbol)}</span>
        </Link>
        {proposal.confidence && (
          <span className="prop-conf" title="模型自評的把握程度，不是勝率">
            信心 {CONFIDENCE_LABEL[proposal.confidence] ?? proposal.confidence}
          </span>
        )}
        <SimBadge />
      </header>

      {proposal.headline && <p className="prop-headline">{proposal.headline}</p>}

      <dl className="prop-plan" aria-label="交易計畫">
        <LevelCell label="限價" level={order?.limit_price} reference={reference} />
        <Cell label="張數" value={order?.lots != null ? `${order.lots} 張` : dash} />
        <Cell label="預估金額" value={order?.est_amount != null ? `NT$ ${formatMoney(order.est_amount)}` : dash} />
        <LevelCell label="停損" level={proposal.stop_loss} reference={reference} />
        <LevelCell label="停利" level={proposal.take_profit} reference={reference} />
        <Cell label="觀察天數" value={proposal.horizon_days != null ? `${proposal.horizon_days} 天` : dash} />
      </dl>
      {reference != null && (
        <p className="prop-note">
          參考價 <span className="num">{formatPrice(reference)}</span>（最近收盤{proposal.as_of ? `，${proposal.as_of}` : ""}
          ）；百分比是各價位相對參考價的距離。
        </p>
      )}

      {(signal || proposal.aligned === false) && (
        <section className="prop-signal" aria-label="與量化訊號">
          <h4 className="prop-title">與量化訊號</h4>
          {signal && (
            <p>
              量化訊號：<strong>{signalCopy?.label ?? signal.action ?? dash}</strong>
              {signal.score != null && (
                <>
                  ，分數 <span className="num">{signal.score.toFixed(1)}</span>
                </>
              )}
              {signal.as_of && <small className="num">（{signal.as_of}）</small>}
              {proposal.aligned === true && <span className="prop-aligned"> · 方向一致</span>}
            </p>
          )}
          {proposal.aligned === false && (
            <p className="prop-warn" role="note">
              <strong>與量化訊號不一致</strong>
              <span>AI 的方向和確定性的量化訊號相反，請特別留意下方的理由與風險。</span>
            </p>
          )}
        </section>
      )}

      {Array.isArray(proposal.checks) && (
        <section className="prop-checks" aria-label="系統檢查">
          <h4 className="prop-title">
            系統檢查
            {!noisyChecks && <small>沒有阻擋或調整的項目</small>}
          </h4>
          {checks.blocked.length > 0 && (
            <ul className="prop-check-list">
              {checks.blocked.map((check, index) => (
                <li key={`b${index}`} className="prop-check blocked">
                  <strong>無法照提案下單</strong>
                  <span>{check.message ?? check.code ?? dash}</span>
                </li>
              ))}
            </ul>
          )}
          {checks.adjusted.length > 0 && (
            <ul className="prop-check-list">
              {checks.adjusted.map((check, index) => (
                <li key={`a${index}`} className="prop-check adjusted">
                  <strong>系統已調整</strong>
                  <span>{check.message ?? check.code ?? dash}</span>
                </li>
              ))}
            </ul>
          )}
          {checks.info.length > 0 && (
            <ul className="prop-check-list">
              {checks.info.map((check, index) => (
                <li key={`i${index}`} className="prop-check info">
                  <span>{check.message ?? check.code ?? dash}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {sizing && (
        <section className="prop-sizing" aria-label="部位與風險">
          <h4 className="prop-title">部位與風險</h4>
          <dl className="prop-plan">
            <Cell label="佔資產" value={formatRatioPct(sizing.position_pct)} />
            <Cell
              label="最大可能虧損"
              value={hasLoss ? `NT$ ${formatMoney(sizing.risk_amount as number)}` : dash}
              hint={hasLoss ? `${formatRatioPct(sizing.risk_pct)} 的資產` : proposal.stop_loss == null ? "未設停損，無法估算" : undefined}
            />
            <Cell label="可用現金" value={sizing.cash_available != null ? `NT$ ${formatMoney(sizing.cash_available)}` : dash} />
          </dl>
        </section>
      )}

      {(proposal.rationale ||
        proposal.key_evidence?.length ||
        proposal.risks?.length ||
        proposal.invalidation?.length) && (
        <details className="prop-more">
          <summary>理由、證據與風險</summary>
          {proposal.rationale && <p className="prop-text">{proposal.rationale}</p>}
          <List title="主要證據" items={proposal.key_evidence} />
          <List title="風險" items={proposal.risks} />
          <List title="失效條件" items={proposal.invalidation} />
          {proposal.evidence_refs?.length ? (
            <p className="prop-note mono">引用：{proposal.evidence_refs.join("、")}</p>
          ) : null}
        </details>
      )}

      {pending ? (
        <div className="prop-actions">
          {hold ? (
            <>
              <p className="prop-note">AI 建議觀望，沒有委託可帶入。</p>
              <button type="button" className="button small" disabled={busy} onClick={() => void reject(null)}>
                {busy ? "處理中…" : "收起"}
              </button>
            </>
          ) : rejecting ? (
            <form
              className="prop-reject"
              onSubmit={(event) => {
                event.preventDefault();
                void reject(reason.trim() || null);
              }}
            >
              <label htmlFor={reasonId} className="prop-note">
                不採用的原因（選填）
              </label>
              <input
                id={reasonId}
                className="input"
                value={reason}
                maxLength={200}
                onChange={(event) => setReason(event.target.value)}
                placeholder="例如：價位太高"
                autoFocus
              />
              <div className="prop-btn-row">
                <button type="submit" className="button small" disabled={busy}>
                  {busy ? "處理中…" : "確認不採用"}
                </button>
                <button type="button" className="button small" disabled={busy} onClick={() => setRejecting(false)}>
                  取消
                </button>
              </div>
            </form>
          ) : (
            <>
              <div className="prop-btn-row">
                {blockedReason ? (
                  <button type="button" className="button small primary" disabled aria-describedby={`${headingId}-why`}>
                    帶入模擬下單
                  </button>
                ) : onUse ? (
                  <button type="button" className="button small primary" onClick={() => onUse(proposal)}>
                    帶入模擬下單
                  </button>
                ) : (
                  <Link href={proposalHref(proposal.id)} className="button small primary">
                    帶入模擬下單
                  </Link>
                )}
                <button type="button" className="button small" onClick={() => setRejecting(true)}>
                  不採用
                </button>
              </div>
              {blockedReason ? (
                <p className="prop-note" id={`${headingId}-why`}>
                  {blockedReason}。
                </p>
              ) : (
                <p className="prop-note">帶入後仍可修改價格與張數，須你按下確認才會成為模擬委託。</p>
              )}
            </>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
      ) : (
        outcome && (
          <p className="prop-outcome" role="status">
            {outcome}
          </p>
        )
      )}

      <footer className="prop-foot">
        <p className="prop-meta">
          {proposal.model && <span className="mono">{proposal.model}</span>}
          {proposal.prompt_version && <span className="mono"> · {proposal.prompt_version}</span>}
          {proposal.created_at && <span> · 產生於 {formatDateTime(proposal.created_at)}</span>}
          {proposal.expires_at && <span> · 有效至 {formatDateTime(proposal.expires_at)}</span>}
        </p>
        <p className="prop-note">{AI_DISCLAIMER}</p>
        <BacktestLink symbol={proposal.symbol} label="先用回測驗證" />
      </footer>
    </article>
  );
}
