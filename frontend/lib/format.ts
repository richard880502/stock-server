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
