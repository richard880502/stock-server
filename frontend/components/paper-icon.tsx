import type { SVGProps } from "react";

/** Tab icon for 模擬交易: candlesticks over a baseline. Same stroke style as icons.tsx. */
export function PaperIcon({ size = 18, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path d="M7 4v3M7 14v3M12 7v2M12 17v3M17 3v4M17 13v3" />
      <rect x="5" y="7" width="4" height="7" rx="1" />
      <rect x="10" y="9" width="4" height="8" rx="1" />
      <rect x="15" y="7" width="4" height="6" rx="1" />
    </svg>
  );
}
