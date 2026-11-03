/** The Quant Signal mark: a rising signal line inside a rounded square. */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="32" height="32" rx="9" fill="currentColor" />
      <path
        d="M7 21.5 13 15l4 3.5L25 10"
        fill="none"
        stroke="var(--brand-mark-ink)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="25" cy="10" r="2.4" fill="var(--brand-mark-ink)" />
    </svg>
  );
}
