"use client";

import Link from "next/link";
import { signOut, useSession } from "@/lib/api/session";
import { useDashboard } from "@/lib/dashboard-context";
import { BrandMark } from "./brand";
import { LogoutIcon } from "./icons";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";
import { ViewTabs } from "./view-tabs";

function StatusPill({ ok, children }: { ok: boolean | null; children: string }) {
  return (
    <span className="status-pill" data-state={ok === null ? "unknown" : ok ? "ok" : "idle"}>
      <span className="status-dot" aria-hidden="true" />
      <span className="status-label">{children}</span>
    </span>
  );
}

export function Topbar() {
  const { dataStatus, llmStatus } = useDashboard();
  const { user } = useSession();
  return (
    <div className="topbar">
      <Link className="brand" href="/" aria-label="Quant Signal 首頁">
        <BrandMark />
        <span className="brand-text">
          <strong>Quant Signal</strong>
          <small>證據優先的市場研究</small>
        </span>
      </Link>
      <ViewTabs />
      <div className="topbar-end">
        <div className="service-state" aria-label="服務狀態">
          <StatusPill ok={dataStatus ? dataStatus.real_data_ready : null}>
            {dataStatus ? (dataStatus.real_data_ready ? "資料：交易所官方" : "資料：示範") : "資料：—"}
          </StatusPill>
          <StatusPill ok={llmStatus ? llmStatus.configured : null}>
            {llmStatus ? (llmStatus.configured ? "LLM 已連線" : "LLM 待設定") : "LLM：—"}
          </StatusPill>
        </div>
        <ThemeToggle />
        {user ? (
          <UserMenu user={user} />
        ) : (
          <button type="button" className="icon-button" onClick={() => void signOut()} aria-label="登出">
            <LogoutIcon />
            <span className="icon-button-label">登出</span>
          </button>
        )}
      </div>
    </div>
  );
}
