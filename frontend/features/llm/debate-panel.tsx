"use client";

import { EmptyState } from "@/components/empty-state";
import { SparkIcon } from "@/components/icons";
import { StanceChip } from "@/components/status-chip";
import { SyncPendingNotice } from "@/components/sync-pending-notice";
import type { DebateCase } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import { LlmErrorNotice } from "./llm-error";
import { EvidenceColumns, JudgementSummary, ScenarioRow } from "./judgement-detail";

function DebateCaseColumn({
  side,
  debateCase,
  counterLabel,
}: {
  side: "bull" | "bear";
  debateCase: DebateCase;
  counterLabel: string;
}) {
  return (
    <div className={`debate-case ${side}`}>
      <StanceChip stance={side === "bull" ? "bullish" : "bearish"}>
        {side === "bull" ? "多方論點" : "空方論點"}
      </StanceChip>
      <p className="debate-thesis">{debateCase.thesis}</p>
      <h4 className="column-label">關鍵證據</h4>
      <ul className="bullet-list">
        {debateCase.key_evidence.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      {Boolean(debateCase.evidence_refs?.length) && <p className="note">證據來源：{debateCase.evidence_refs?.join("、")}</p>}
      <h4 className="column-label">{counterLabel}</h4>
      <ul className="bullet-list">
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
  const ready = Boolean(signalData.signal) && Boolean(llmStatus?.configured);

  return (
    <div className="llm-body">
      <div className="llm-toolbar">
        <p className="note">
          多方、空方與風控檢視同一截止日的證據，再由裁決者提出條件式研究計畫；通常需要數十秒。
        </p>
        <button
          type="button"
          className="button primary"
          onClick={llm.runDebate}
          disabled={!ready || debating}
        >
          {debating ? "辯論中…" : debateReport ? "重新辯論" : "啟動多空辯論"}
        </button>
      </div>

      {llm.debateError && !debating && (
        <LlmErrorNotice error={llm.debateError} onRetry={llm.runDebate} />
      )}

      {debateSyncWait ? (
        <SyncPendingNotice wait={debateSyncWait} onCancel={llm.cancelDebateSync} />
      ) : debating ? (
        <div className="debate-columns" aria-busy="true" role="status" aria-label="多空辯論進行中">
          {[0, 1].map((i) => (
            <div className="debate-case" key={i}>
              <span className="skeleton line" style={{ width: "30%" }} />
              <span className="skeleton line" />
              <span className="skeleton line" style={{ width: "90%" }} />
              <span className="skeleton line" style={{ width: "70%" }} />
            </div>
          ))}
        </div>
      ) : debateReport ? (
        <div className="llm-result">
          <p className="note">證據截止日：{debateReport.as_of}。{debateReport.data_sufficient === false ? "市場資料為示範資料，維持觀望。" : ""}</p>
          <div className="judgement-card">
            <p className="column-label">裁決</p>
            <JudgementSummary
              judgement={debateReport.judgement}
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
            <p className="note">證據來源：{debateReport.judgement.evidence_refs?.join("、") || "未提供"}</p>
            <ScenarioRow scenarios={debateReport.judgement.scenarios} />
          </div>
          <div className="debate-columns">
            <DebateCaseColumn
              side="bull"
              debateCase={debateReport.bull_case}
              counterLabel="預期空方反駁"
            />
            <DebateCaseColumn
              side="bear"
              debateCase={debateReport.bear_case}
              counterLabel="預期多方反駁"
            />
          </div>
          {debateReport.risk_review && (
            <div className="judgement-card">
              <p className="column-label">風險審查</p>
              <p>{debateReport.risk_review.summary}</p>
              <h4 className="column-label">風險與失效條件</h4>
              <ul className="bullet-list">{[...debateReport.risk_review.risk_warnings, ...debateReport.risk_review.invalidation_conditions].map((item, index) => <li key={index}>{item}</li>)}</ul>
              <p className="note">證據來源：{debateReport.risk_review.evidence_refs.join("、") || "未提供"}</p>
            </div>
          )}
          {Boolean(debateReport.evidence?.length) && (
            <details><summary>查看原始證據與日期</summary>
              {debateReport.evidence?.map((item) => <details key={item.id}><summary>{item.id} · {item.as_of}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(item.details, null, 2)}</pre></details>)}
            </details>
          )}

        </div>
      ) : llm.debateError ? null : (
        <EmptyState icon={<SparkIcon size={22} />} title={llmStatus?.configured ? "尚未啟動辯論" : "LLM 尚未設定"}>
          {llmStatus?.configured
            ? "按下「啟動多空辯論」，兩位研究員會基於同一份證據各自舉證，再由裁決者給出操作指南。"
            : "在後端設定 LLM 後，這裡會顯示多空辯論與最終操作指南。"}
        </EmptyState>
      )}
    </div>
  );
}
