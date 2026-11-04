import type { BackfillStatus } from "./api/types";

/** Backfill states that still need watching; everything else stops the polling. */
export const isActive = (row: BackfillStatus) => row.state === "running" || row.state === "queued";

/** Something worth telling the user: not the quiet steady state. */
export const needsAttention = (row: BackfillStatus) => row.state !== "complete";

/**
 * Plain-language time left from the server's rough `eta_seconds`. Minutes under
 * an hour, hours and 5-minute steps beyond (more precision would be made up).
 */
export function formatEta(seconds: number): string {
  const minutes = Math.max(0, seconds) / 60;
  if (minutes < 1) return "不到 1 分鐘後完成";
  if (minutes < 60) return `約 ${Math.ceil(minutes)} 分鐘後完成`;
  const total = Math.round(minutes / 5) * 5;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest === 0 ? `約 ${hours} 小時後完成` : `約 ${hours} 小時 ${rest} 分後完成`;
}

/** Time-left sentence while a job runs; the other states say nothing about time. */
export function etaLine(row: BackfillStatus): string | null {
  if (row.state !== "running") return null;
  return row.eta_seconds !== null ? formatEta(row.eta_seconds) : "正在估算…";
}

export const stateLabel: Record<BackfillStatus["state"], string> = {
  running: "回補中",
  queued: "排隊中",
  idle: "待下次更新",
  complete: "已完成",
};

/** Share of the sessions a score needs that are stored, 0-100 whole percent. */
export const scoreProgressPct = (row: BackfillStatus) =>
  Math.min(100, Math.round((row.stored_sessions / Math.max(1, row.sessions_needed_for_score)) * 100));

/**
 * Whether the market score is current, as short copy; null when it is (nothing to
 * explain). Never promises a time: the ETA belongs to the whole history, not the score.
 */
export function scoreLine(row: BackfillStatus): string | null {
  if (row.score_ready) return null;
  const active = isActive(row);
  if (row.stored_sessions < row.sessions_needed_for_score) {
    const base = `大盤分數需累積 ${row.sessions_needed_for_score} 個交易日，目前已存 ${row.stored_sessions} 個（約 ${scoreProgressPct(row)}%）`;
    if (row.state === "running") return `${base}，回補進行中。`;
    if (row.state === "queued") return `${base}，已排入回補。`;
    return `${base}，下次定時更新會繼續累積。`;
  }
  return active ? "分數更新中。" : "分數尚未產生，會在下次定時更新計算。";
}
