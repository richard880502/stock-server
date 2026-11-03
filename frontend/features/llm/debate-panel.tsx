"use client";

import { StanceChip } from "@/components/status-chip";
import { SyncPendingNotice } from "@/components/sync-pending-notice";
import type { DebateCase } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import { EvidenceColumns, JudgementSummary, ScenarioRow } from "./judgement-detail";
import { LlmPanelHeader } from "./llm-panel-header";

function DebateCaseColumn({
  side,
  label,
  debateCase,
  counterLabel,
}: {
  side: "bull" | "bear";
  label: string;
  debateCase: DebateCase;
  counterLabel: string;
}) {
  return (
    <div className={`debate-case ${side}`}>
      <StanceChip stance={side === "bull" ? "bullish" : "bearish"}>{label}</StanceChip>
      <p>{debateCase.thesis}</p>
      <span className="column-label">關鍵證據</span>
      <ul>
        {debateCase.key_evidence.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <span className="column-label warning">{counterLabel}</span>
      <ul>
        {debateCase.counterpoints_to_address.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function DebatePanel() {
  const { signalData, llmStatus, llm } = useDashboard();
  const { debateReport, debating, debateSyncWait } = llm;

  return (
    <section className="llm-panel debate-panel">
      <LlmPanelHeader
        eyebrow="BULL / BEAR DEBATE"
        title="多空各自舉證，再由裁決者給出操作指南。"
        llmStatus={llmStatus}
        action={
          <button
            type="button"
            className="secondary-button"
            onClick={llm.runDebate}
            disabled={!signalData.signal || debating || !llmStatus?.configured}
          >
            {debating ? "辯論中…" : "啟動多空辯論"}
          </button>
        }
      />

      {debateSyncWait ? (
        <SyncPendingNotice wait={debateSyncWait} onCancel={llm.cancelDebateSync} />
      ) : debateReport ? (
        <div className="llm-result">
          <div className="debate-columns">
            <DebateCaseColumn
              side="bull"
              label="多方"
              debateCase={debateReport.bull_case}
              counterLabel="預期空方反駁"
            />
            <DebateCaseColumn
              side="bear"
              label="空方"
              debateCase={debateReport.bear_case}
              counterLabel="預期多方反駁"
            />
          </div>
          <JudgementSummary
            judgement={debateReport.judgement}
            className="llm-summary judgement-summary"
            heading={
              <div className="judgement-heading">
                <StanceChip stance={debateReport.judgement.stance} />
                {debateReport.judge_model !== debateReport.model && (
                  <small>獨立裁決模型 · {debateReport.judge_model}</small>
                )}
              </div>
            }
          />
          <EvidenceColumns evidenceLabel="裁決證據" judgement={debateReport.judgement} />
          <ScenarioRow scenarios={debateReport.judgement.scenarios} />
        </div>
      ) : (
        <div className="llm-empty">
          <p>
            {llmStatus?.configured
              ? "按下「啟動多空辯論」，多方與空方研究員會各自基於同一份證據舉證，再由裁決者給出最終操作指南。"
              : "在後端設定 LLM 後，這裡會顯示多空辯論與最終操作指南。"}
          </p>
        </div>
      )}
    </section>
  );
}
