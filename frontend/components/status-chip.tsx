import type { ReactNode } from "react";
import type { BacktestJob, Judgement, Regime } from "@/lib/api/types";
import { regimeCopy, regimeGloss, regimeTone, stanceCopy, type Tone } from "@/lib/copy";
import { ArrowDownIcon, ArrowUpIcon, AlertIcon, MinusIcon } from "./icons";

const toneIcon = {
  up: ArrowUpIcon,
  down: ArrowDownIcon,
  flat: MinusIcon,
  warn: AlertIcon,
} as const;

export function ToneChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = toneIcon[tone];
  return (
    <span className={`chip tone-${tone}`}>
      <Icon size={14} />
      {children}
    </span>
  );
}

export function RegimeChip({ regime }: { regime?: Regime | null }) {
  if (!regime) return <span className="chip plain">—</span>;
  return (
    <ToneChip tone={regimeTone[regime]}>
      {regimeCopy[regime]}
      <span className="chip-gloss">· {regimeGloss[regime]}</span>
    </ToneChip>
  );
}

const jobCopy: Record<BacktestJob["status"], string> = {
  queued: "排隊中",
  running: "計算中",
  completed: "已完成",
  failed: "失敗",
};

export function JobStatusChip({
  status,
  children,
}: {
  status?: BacktestJob["status"];
  children?: ReactNode;
}) {
  const tone = status === "failed" ? "warn" : "flat";
  return (
    <span className={`chip tone-${tone} job-${status ?? "idle"}`}>
      {status === "running" && <span className="spinner small" aria-hidden="true" />}
      {children ?? (status ? jobCopy[status] : "—")}
    </span>
  );
}

export function StanceChip({
  stance,
  children,
}: {
  stance: Judgement["stance"];
  children?: ReactNode;
}) {
  const { label, tone } = stanceCopy[stance];
  return <ToneChip tone={tone}>{children ?? label}</ToneChip>;
}

/** Model / connection label; accent-coloured once the backend LLM is configured. */
export function ConnectionChip({
  connected,
  children,
}: {
  connected: boolean;
  children: ReactNode;
}) {
  return (
    <span className="status-pill" data-state={connected ? "ok" : "idle"}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
