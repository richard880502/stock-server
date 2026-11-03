export const today = () => {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
};

export const formatPct = (value: number) =>
  new Intl.NumberFormat("zh-TW", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);

export const formatNumber = (value: number, digits = 1) =>
  new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);

/** Fixed one-decimal percentage that renders null as an em dash. */
export const formatRatio = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export const formatPrice = (value: number) =>
  value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });

/** Explicit sign with a true minus sign, so direction never depends on colour. */
export const formatSigned = (value: number, digits = 1) => {
  const text = Math.abs(value).toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  if (value > 0) return `+${text}`;
  if (value < 0) return `−${text}`;
  return text;
};

export const formatSignedPct = (ratio: number, digits = 1) =>
  `${formatSigned(ratio * 100, digits)}%`;

/** Direction of a number for tone classes. */
export const directionOf = (value: number): "up" | "down" | "flat" =>
  value > 0 ? "up" : value < 0 ? "down" : "flat";

/** Trading-day ISO date → "2026-11-03" is already compact; this shortens to "11/03". */
export const shortDate = (iso: string) => iso.slice(5).replace("-", "/");

/** 張 (1,000-share lots) with thousands separators; fractional lots are rounded. */
export const formatLots = (value: number) =>
  Math.round(value).toLocaleString("zh-TW", { maximumFractionDigits: 0 });

export const formatSignedLots = (value: number) => formatSigned(Math.round(value), 0);

/** NT$ amounts in the units Taiwanese readers expect: 1.2 兆, 345.6 億, 4,321 萬. */
export function formatTwdCompact(value: number): string {
  const abs = Math.abs(value);
  const group = (n: number, digits: number) =>
    n.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (abs >= 1e12) return `${group(value / 1e12, 2)} 兆`;
  if (abs >= 1e8) return `${group(value / 1e8, abs >= 1e10 ? 0 : 1)} 億`;
  if (abs >= 1e4) return `${group(value / 1e4, 0)} 萬`;
  return `${group(value, 0)} 元`;
}

/** NT$ → 億 as a plain number string, for charts and tables where the unit is in the heading. */
export const formatYi = (twd: number, digits = 1) => formatNumber(twd / 1e8, digits);

export const formatSignedYi = (twd: number, digits = 1) => formatSigned(twd / 1e8, digits);
