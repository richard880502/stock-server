import type { ReactNode } from "react";
import { ConnectionChip } from "@/components/status-chip";
import type { LlmStatus } from "@/lib/api/types";

export function LlmPanelHeader({
  eyebrow,
  title,
  llmStatus,
  action,
}: {
  eyebrow: string;
  title: string;
  llmStatus: LlmStatus | null;
  action: ReactNode;
}) {
  return (
    <div className="llm-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      <div className="llm-actions">
        <ConnectionChip connected={Boolean(llmStatus?.configured)}>
          {llmStatus?.configured ? llmStatus.model : "OpenAI-compatible model 未設定"}
        </ConnectionChip>
        {action}
      </div>
    </div>
  );
}
