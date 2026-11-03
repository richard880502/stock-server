"use client";

import Link from "next/link";
import { clearApiKey } from "@/lib/api/auth";
import { useDashboard } from "@/lib/dashboard-context";

export function Topbar() {
  const { dataStatus, llmStatus } = useDashboard();
  return (
    <header className="topbar">
      <Link className="brand" href="/" aria-label="Quant Signal 首頁">
        <span className="brand-mark" aria-hidden="true">
          QS
        </span>
        <span>
          <strong>Quant Signal</strong>
          <small>Evidence-first market intelligence</small>
        </span>
      </Link>
      <div className="service-state">
        <span className="live-dot" aria-hidden="true" />
        本機量化服務
        <span className="service-separator" aria-hidden="true" />
        資料 {dataStatus?.real_data_ready ? "交易所官方" : "示範"}
        <span className="service-separator" aria-hidden="true" />
        LLM {llmStatus?.configured ? "已連線" : "待設定"}
        <span className="service-separator" aria-hidden="true" />
        <button type="button" className="logout-link" onClick={clearApiKey}>
          登出
        </button>
      </div>
    </header>
  );
}
