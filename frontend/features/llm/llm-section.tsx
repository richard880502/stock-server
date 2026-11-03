"use client";

import { useState } from "react";
import { Card } from "@/components/card";
import { ConnectionChip } from "@/components/status-chip";
import { useDashboard } from "@/lib/dashboard-context";
import { DebatePanel } from "./debate-panel";
import { LlmGuidancePanel } from "./llm-guidance-panel";

type Tab = "guide" | "debate";

/**
 * LLM explanations behind one disclosure-friendly card: the streamed operation
 * guide and the bull / bear debate share a tab strip so the page stays calm
 * until the reader asks for them. Both keep their results while hidden.
 */
export function LlmSection() {
  const { llmStatus } = useDashboard();
  const [tab, setTab] = useState<Tab>("guide");
  const tabs: { id: Tab; label: string }[] = [
    { id: "guide", label: "操作指南" },
    { id: "debate", label: "多空辯論" },
  ];

  return (
    <Card
      title="AI 解讀"
      sub="模型只整理上方已有的證據，不會新增數據；輸出可能出錯，請以量化證據為準。"
      aside={
        <ConnectionChip connected={Boolean(llmStatus?.configured)}>
          {llmStatus?.configured ? (llmStatus.model ?? "LLM 已連線") : "LLM 尚未設定"}
        </ConnectionChip>
      }
      className="llm-card"
      aria-label="AI 解讀"
    >
      <div className="segmented" role="tablist" aria-label="AI 解讀類型">
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`llm-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`llm-panel-${id}`}
            tabIndex={tab === id ? 0 : -1}
            onClick={() => setTab(id)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                const next = tab === "guide" ? "debate" : "guide";
                setTab(next);
                document.getElementById(`llm-tab-${next}`)?.focus();
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`llm-panel-${tab}`}
        aria-labelledby={`llm-tab-${tab}`}
        className="llm-panel"
      >
        {tab === "guide" ? <LlmGuidancePanel /> : <DebatePanel />}
      </div>
    </Card>
  );
}
