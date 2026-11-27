import { formatPrice, formatSigned } from "@/lib/format";
import { Direction } from "@/components/direction";
import type { ScreenerEntry } from "@/lib/api/types";
import {
  cellValue,
  type ColumnDef,
  flagLabel,
  formatScore,
  formatScreenerValue,
  splitHighlights,
  toneOf,
} from "./screener-model";

const clamp = (value: number) => Math.max(0, Math.min(100, value));

/** A 0–100 score as a number plus a thin bar; missing scores are an em dash with no bar. */
export function ScoreBar({ value, digits = 0 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined) {
    return <span className="sc-score text-muted">—</span>;
  }
  return (
    <span className="sc-score">
      <span className="sc-score-num num">{digits === 0 ? formatScore(value) : value.toFixed(digits)}</span>
      <span className="sc-bar" aria-hidden="true">
        <span className="sc-bar-fill" style={{ width: `${clamp(value)}%` }} />
      </span>
    </span>
  );
}

/** One cell of a preset column: scores as number + bar, metrics by their meta unit (red/green when signed). */
export function ColumnValue({ entry, column }: { entry: ScreenerEntry; column: ColumnDef }) {
  const value = cellValue(entry, column.key);
  if (column.kind === "score") return <ScoreBar value={typeof value === "number" ? value : null} />;
  const tone = toneOf(value, column.signed);
  return (
    <span className={`sc-value num ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : ""}`}>
      {formatScreenerValue(value, { key: column.key, unit: column.unit, decimals: column.decimals, signed: column.signed })}
    </span>
  );
}

export function FlagBadges({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <>
      {flags.map((flag) => (
        <span key={flag} className={`sc-flag ${flag === "disposition" || flag === "處置" ? "strong" : ""}`}>
          {flagLabel(flag)}
        </span>
      ))}
    </>
  );
}

export function HighlightChips({ highlights, all = false }: { highlights: string[]; all?: boolean }) {
  if (highlights.length === 0) return <span className="text-muted">—</span>;
  const { shown, hidden } = splitHighlights(highlights, all ? highlights.length : 3);
  const folded = highlights.slice(highlights.length - hidden);
  return (
    <ul className="sc-highlights" aria-label="亮點">
      {shown.map((text) => (
        <li key={text} className="sc-highlight">
          {text}
        </li>
      ))}
      {hidden > 0 && (
        <li className="sc-highlight more" title={folded.join("\n")} aria-label={`另有 ${hidden} 項：${folded.join("、")}`}>
          +{hidden}
        </li>
      )}
    </ul>
  );
}

export function PriceCell({ entry }: { entry: ScreenerEntry }) {
  return (
    <>
      <span className="num">{formatPrice(entry.close)}</span>
      {entry.change_pct === null ? (
        <span className="text-muted num">—</span>
      ) : (
        <Direction value={entry.change_pct} className="num">
          {formatSigned(entry.change_pct, 2)}%
        </Direction>
      )}
    </>
  );
}
