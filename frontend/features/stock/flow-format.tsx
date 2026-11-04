import { Direction } from "@/components/direction";
import { formatSigned, formatSignedLots, shortDate } from "@/lib/format";

/** A flow in lots (張): signed and coloured by direction, or an honest "尚未公布" for null. */
export function Lots({
  value,
  arrow = false,
  empty = "尚未公布",
}: {
  value: number | null;
  arrow?: boolean;
  empty?: string;
}) {
  if (value === null) return <span className="text-muted">{empty}</span>;
  return (
    <Direction value={value} arrow={arrow}>
      {formatSignedLots(value)}
    </Direction>
  );
}

/** Signed percentage in percent units (the API sends 1.5 for +1.5%), or an em dash. */
export function PctChange({
  value,
  arrow = true,
  digits = 2,
}: {
  value: number | null;
  arrow?: boolean;
  digits?: number;
}) {
  if (value === null) return <span className="text-muted">—</span>;
  return (
    <Direction value={value} arrow={arrow}>
      {formatSigned(value, digits)}%
    </Direction>
  );
}

/**
 * The institutional report of the newest session usually lands hours after the
 * quotes. Returns the sentence that says so, or null when flows are current.
 */
export function flowsLagNote(flowsThrough: string | null, dataThrough: string | null): string | null {
  if (!dataThrough) return null;
  if (!flowsThrough) return "法人資料尚未公布";
  if (flowsThrough < dataThrough) {
    return `法人資料更新至 ${shortDate(flowsThrough)}（${shortDate(dataThrough)} 的報表尚未公布）`;
  }
  return null;
}
