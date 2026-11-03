import { ArrowDownIcon, ArrowUpIcon, MinusIcon, PauseIcon } from "@/components/icons";
import { RegimeChip } from "@/components/status-chip";
import type { Signal, SignalAction } from "@/lib/api/types";
import { actionCopy, actionTone, factorLabels } from "@/lib/copy";
import { formatNumber, formatPct } from "@/lib/format";

const actionIcon: Record<SignalAction, typeof ArrowUpIcon> = {
  increase: ArrowUpIcon,
  hold: MinusIcon,
  reduce: ArrowDownIcon,
  avoid: PauseIcon,
};

/** One-sentence evidence summary: strongest / weakest factor and alert count. */
function evidenceSummary(signal: Signal, alertCount: number) {
  const entries = Object.entries(signal.factors) as [keyof Signal["factors"], number][];
  const strongest = entries.reduce((a, b) => (b[1] > a[1] ? b : a));
  const weakest = entries.reduce((a, b) => (b[1] < a[1] ? b : a));
  return { strongest, weakest, alertCount };
}

export function VerdictHero({
  signal,
  name,
  alertCount,
  refreshing,
}: {
  signal: Signal;
  name: string | null;
  alertCount: number;
  refreshing?: boolean;
}) {
  const copy = actionCopy[signal.action];
  const tone = actionTone[signal.action];
  const Icon = actionIcon[signal.action];
  const { strongest, weakest } = evidenceSummary(signal, alertCount);
  const score = Math.round(signal.score);

  return (
    <section
      className={`hero tone-${tone}${refreshing ? " is-refreshing" : ""}`}
      aria-labelledby="hero-title"
    >
      <div className="hero-main">
        <div className="hero-id">
          <span className="hero-symbol mono">{signal.symbol}</span>
          {name && <span className="hero-name">{name}</span>}
          <span className="hero-asof num">資料截止 {signal.as_of}</span>
        </div>
        <p className="hero-kicker">量化訊號</p>
        <h1 id="hero-title" className="hero-action">
          <span className="hero-action-icon" aria-hidden="true">
            <Icon size={28} />
          </span>
          <span className="sr-only">{signal.symbol}：</span>
          {copy.label}
        </h1>
        <p className="hero-guide">{copy.guide}</p>
        <div className="hero-chips">
          <RegimeChip regime={signal.regime} />
          <span className="chip plain">
            {formatNumber(signal.observations, 0)} 筆觀察 · {signal.strategy_version}
          </span>
        </div>
      </div>

      <div className="hero-metrics">
        <div className="hero-score">
          <div className="hero-score-line">
            <span className="hero-score-num num" aria-label={`綜合分數 ${score}，滿分 100`}>
              {score}
            </span>
            <span className="hero-score-max" aria-hidden="true">
              / 100
            </span>
          </div>
          <div className="score-track" aria-hidden="true">
            <span className="score-tick" style={{ left: "50%" }} />
            <span className="score-fill" style={{ width: `${Math.max(2, Math.min(100, signal.score))}%` }} />
          </div>
          <span className="stat-label">綜合分數</span>
        </div>
        <div className="hero-confidence">
          <div className="hero-confidence-line">
            <span className="stat-label">信心度</span>
            <strong className="num">{formatPct(signal.confidence)}</strong>
          </div>
          <div className="meter" aria-hidden="true">
            <span style={{ width: `${signal.confidence * 100}%` }} />
          </div>
        </div>
        <p className="hero-summary">
          最強因子 <b>{factorLabels[strongest[0]]}</b>{" "}
          <span className="num">{formatNumber(strongest[1], 0)}</span>，最弱{" "}
          <b>{factorLabels[weakest[0]]}</b> <span className="num">{formatNumber(weakest[1], 0)}</span>
          ；{alertCount > 0 ? `${alertCount} 項異常提示` : "未偵測到重大異常"}。
        </p>
      </div>

      <p className="hero-disclaimer">
        分數與信心度是模型輸出，用來整理證據，不構成投資建議，也不代表未來勝率。
      </p>
    </section>
  );
}

export function VerdictSkeleton() {
  return (
    <section className="hero" aria-busy="true" aria-label="正在計算訊號">
      <div className="hero-main">
        <span className="skeleton line" style={{ width: "40%" }} />
        <span className="skeleton line" style={{ width: "18%", marginTop: 20 }} />
        <span className="skeleton block" style={{ width: "55%", height: 52, marginTop: 8 }} />
        <span className="skeleton line" style={{ width: "80%", marginTop: 12 }} />
        <span className="skeleton line" style={{ width: "62%" }} />
      </div>
      <div className="hero-metrics">
        <span className="skeleton block" style={{ width: "50%", height: 64 }} />
        <span className="skeleton line" style={{ width: "100%" }} />
        <span className="skeleton line" style={{ width: "90%" }} />
      </div>
    </section>
  );
}
