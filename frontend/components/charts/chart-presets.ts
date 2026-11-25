/** Chart presets shared by the candle chart and its preference parsing (no chart library). */

export const MA_PERIODS = [5, 20, 60] as const;
export type MaPeriod = (typeof MA_PERIODS)[number];

/** Visible window presets, in trading days ("all" = everything loaded). */
export const RANGE_PRESETS = [
  { key: "3m", label: "3月", bars: 63 },
  { key: "6m", label: "6月", bars: 126 },
  { key: "1y", label: "1年", bars: 252 },
  { key: "all", label: "全部", bars: Infinity },
] as const;
export type RangeKey = (typeof RANGE_PRESETS)[number]["key"];
