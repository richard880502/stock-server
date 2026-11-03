import type { ReactNode } from "react";
import type { BacktestJob, Judgement, Regime } from "@/lib/api/types";
import { regimeCopy } from "@/lib/copy";

export function RegimeChip({ regime }: { regime?: Regime | null }) {
  return (
    <span className={`regime ${regime ?? "neutral"}`}>
      {regime ? regimeCopy[regime] : "—"}
    </span>
  );
}

export function JobStatusChip({
  status,
  children,
}: {
  status?: BacktestJob["status"];
  children: ReactNode;
}) {
  return <span className={`job-status ${status ?? ""}`.trim()}>{children}</span>;
}

export function StanceChip({
  stance,
  children,
}: {
  stance: Judgement["stance"];
  children?: ReactNode;
}) {
  return <span className={`stance ${stance}`}>{children ?? stance}</span>;
}

/** Model / connection label that turns teal once the backend LLM is configured. */
export function ConnectionChip({
  connected,
  children,
}: {
  connected: boolean;
  children: ReactNode;
}) {
  return <span className={connected ? "connected" : ""}>{children}</span>;
}
