import type { BackfillStatus } from "@/lib/api/types";
import { etaLine, needsAttention, scoreLine, stateLabel, isActive } from "@/lib/backfill";
import { formatNumber } from "@/lib/format";
import { ClockIcon } from "./icons";

const NAME: Record<BackfillStatus["market"], string> = { TW: "上市", TWO: "上櫃" };

function Row({ row }: { row: BackfillStatus }) {
  const name = NAME[row.market] ?? row.market;
  const percent = Math.min(100, Math.max(0, row.percent));
  const eta = etaLine(row);
  const score = scoreLine(row);
  const label = stateLabel[row.state];
  const valueText = `${name}資料回補 ${formatNumber(percent, 1)}%，${label}${eta ? `，${eta}` : ""}`;
  return (
    <li className="backfill-row" data-state={row.state}>
      <div className="backfill-head">
        <span className="backfill-name">{name}</span>
        <span className="backfill-state">
          {row.state === "running" && <span className="spinner small" aria-hidden="true" />}
          {label}
        </span>
        <span className="backfill-pct num">{formatNumber(percent, 1)}%</span>
      </div>
      <div
        className="backfill-bar"
        role="progressbar"
        aria-label={`${name}資料回補進度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={valueText}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <p className="backfill-meta num">
        {eta && <span>{eta}</span>}
        <span>
          已回補 {row.days_done} / {row.days_total} 個交易日
        </span>
      </p>
      {score && <p className="backfill-score">{score}</p>}
    </li>
  );
}

/**
 * Whole-market history backfill, one bar per exchange. Renders nothing when every
 * exchange is complete (the steady state has nothing to say).
 */
export function BackfillProgress({ rows }: { rows: BackfillStatus[] }) {
  if (!rows.some(needsAttention)) return null;
  const active = rows.some(isActive);
  return (
    <section className="backfill" aria-labelledby="backfill-title">
      <div className="backfill-intro">
        <ClockIcon size={18} className="backfill-icon" />
        <div>
          <h2 id="backfill-title" className="backfill-title">
            全市場資料回補
          </h2>
          <p className="backfill-sub">
            {active
              ? "由最近的交易日開始往回補，排行與資金流會逐步完整，頁面會自動更新。"
              : "歷史資料尚未完整，目前沒有進行中的回補；平日 15:30 與 21:45（台北時間）的定時更新會接著補。"}
          </p>
        </div>
      </div>
      <ul className="backfill-list">
        {rows.map((row) => (
          <Row key={row.market} row={row} />
        ))}
      </ul>
    </section>
  );
}

export function BackfillProgressSkeleton() {
  return (
    <section className="backfill" aria-busy="true" aria-label="資料回補進度載入中">
      <span className="skeleton line" style={{ width: "30%" }} />
      <div className="backfill-list">
        {[0, 1].map((i) => (
          <div key={i} className="backfill-row">
            <span className="skeleton line" style={{ width: "40%" }} />
            <span className="skeleton line" style={{ height: "0.5rem" }} />
          </div>
        ))}
      </div>
    </section>
  );
}
