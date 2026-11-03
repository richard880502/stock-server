"use client";

import type { ReactNode } from "react";
import { useDashboard } from "@/lib/dashboard-context";
import { ErrorBanner } from "./error-banner";
import { QueryBar } from "./query-bar";
import { SyncPendingNotice } from "./sync-pending-notice";
import { Topbar } from "./topbar";
import { ViewTabs } from "./view-tabs";

/** Chrome shared by every route: top bar, intro, query bar, banners, tabs, footer. */
export function DashboardFrame({ children }: { children: ReactNode }) {
  const { error, setError, signalData } = useDashboard();
  const { marketEnvironment, syncWait, cancelSync } = signalData;

  return (
    <main>
      <Topbar />

      <section className="workspace">
        <div className="intro-row">
          <div>
            <p className="eyebrow">DAILY DECISION DESK</p>
            <h1>把市場雜訊，整理成可驗證的決策條件。</h1>
          </div>
          <p className="intro-copy">
            量化模型負責計算，LLM 負責解讀。每一個操作觀點，都保留原始因子、
            異常證據與歷史回測。
          </p>
        </div>

        <QueryBar />

        {syncWait && <SyncPendingNotice wait={syncWait} onCancel={cancelSync} />}
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        <ViewTabs />

        {children}

        <footer>
          <span>Quant Signal Server · Research environment</span>
          <span>
            市場資料時間：
            {marketEnvironment?.data_available_at.replace("T", " ").slice(0, 16) ?? "—"}
          </span>
          <span>研究工具，不構成投資建議</span>
        </footer>
      </section>
    </main>
  );
}
