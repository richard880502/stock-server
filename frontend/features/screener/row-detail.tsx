import { WatchlistStar } from "@/components/watchlist-star";
import type { ScreenerEntry, ScreenerMeta } from "@/lib/api/types";
import { cellValue, formatScreenerValue, resolveColumn, toneOf } from "./screener-model";
import { HighlightChips, ScoreBar } from "./screener-parts";

/** How one score is built: its inputs and weights, straight from the meta (nothing hard-coded). */
function Composition({ meta, factorKey }: { meta: ScreenerMeta; factorKey: string }) {
  const factor = meta.factors.find((f) => f.key === factorKey);
  if (!factor || factor.inputs.length === 0) return null;
  return (
    <p className="sc-composition">
      {factor.description && <span>{factor.description} </span>}
      <span>
        由{" "}
        {factor.inputs
          .map((input) => {
            const label = resolveColumn(meta, input.key.replace(/^-/, "")).label;
            const invert = input.invert || input.key.startsWith("-");
            const note = input.absolute
              ? invert ? "（取絕對值，越小越好）" : "（取絕對值，越大越好）"
              : invert ? "（越低越好）" : "";
            return `${label}${note} ${Math.round(input.weight * 100)}%`;
          })
          .join("、")}{" "}
        組成。
      </span>
    </p>
  );
}

/** The expanded part of a row: every score with its recipe, every metric by group, and the actions. */
export function RowDetail({
  entry,
  meta,
  onSignal,
  onBacktest,
}: {
  entry: ScreenerEntry;
  meta: ScreenerMeta;
  onSignal: () => void;
  onBacktest: () => void;
}) {
  const scoreKeys = [
    ...meta.factors.map((f) => f.key),
    ...Object.keys(entry.scores).filter((key) => !meta.factors.some((f) => f.key === key)),
  ].filter((key, i, all) => key !== "composite" && all.indexOf(key) === i);
  const groups = meta.groups.filter((g) => g.key !== "score");
  return (
    <div className="sc-detail">
      <div className="sc-detail-actions">
        <button type="button" className="button small primary" onClick={onSignal}>
          查看訊號
        </button>
        <button type="button" className="button small" onClick={onBacktest}>
          策略回測
        </button>
        <WatchlistStar symbol={entry.symbol} variant="labeled" />
      </div>

      {entry.highlights.length > 0 && (
        <section className="sc-detail-section">
          <h3>亮點</h3>
          <HighlightChips highlights={entry.highlights} all />
        </section>
      )}

      <section className="sc-detail-section">
        <h3>因子分數（0–100，全市場百分位）</h3>
        <ul className="sc-score-list">
          <li>
            <span className="sc-score-name">綜合</span>
            <ScoreBar value={entry.composite} digits={1} />
          </li>
          {scoreKeys.map((key) => (
            <li key={key}>
              <span className="sc-score-name">{resolveColumn(meta, key).label}</span>
              <ScoreBar value={entry.scores[key] ?? null} digits={1} />
              <Composition meta={meta} factorKey={key} />
            </li>
          ))}
        </ul>
        <p className="note">綜合分數把有資料的因子依權重合成；缺資料的因子不計入，也不會被當成 0 分。</p>
      </section>

      {groups.map((group) => {
        const metrics = meta.metrics.filter((m) => m.group === group.key);
        if (metrics.length === 0) return null;
        return (
          <section key={group.key} className="sc-detail-section">
            <h3>{group.label}</h3>
            <dl className="sc-metric-grid">
              {metrics.map((metric) => {
                const value = cellValue(entry, metric.key);
                const column = resolveColumn(meta, metric.key);
                const tone = toneOf(value, column.signed);
                return (
                  <div key={metric.key} title={metric.description || undefined}>
                    <dt>{metric.label}</dt>
                    <dd className={`num ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : ""}`}>
                      {formatScreenerValue(value, { key: metric.key, unit: metric.unit, decimals: metric.decimals, signed: column.signed })}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </section>
        );
      })}
    </div>
  );
}
