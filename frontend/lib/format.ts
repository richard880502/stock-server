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
