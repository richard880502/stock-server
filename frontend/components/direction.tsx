import type { ReactNode } from "react";
import { directionOf } from "@/lib/format";
import { ArrowDownIcon, ArrowUpIcon, MinusIcon } from "./icons";

/**
 * A value coloured by direction (red up / green down) that also carries an
 * arrow, so the meaning survives without colour. Pass preformatted `children`
 * (use formatSigned / formatSignedPct so the sign is in the text too).
 */
export function Direction({
  value,
  children,
  arrow = true,
  className = "",
}: {
  value: number;
  children: ReactNode;
  arrow?: boolean;
  className?: string;
}) {
  const direction = directionOf(value);
  const Icon = direction === "up" ? ArrowUpIcon : direction === "down" ? ArrowDownIcon : MinusIcon;
  return (
    <span className={`direction text-${direction === "flat" ? "muted" : direction} ${className}`}>
      {arrow && <Icon size={14} />}
      <span>{children}</span>
    </span>
  );
}
