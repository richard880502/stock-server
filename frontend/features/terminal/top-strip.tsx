"use client";

import { regimeCopy, regimeGloss, regimeTone } from "@/lib/copy";
import { useDashboard } from "@/lib/dashboard-context";
import { useNow } from "./rail-now";
import { marketSession, SESSION_LABEL, taipeiClock } from "./rail-session";
import type { LiveState } from "./types";
import "./rail.css";

const STATUS_LABEL: Record<LiveState["status"], string> = {
  live: "即時連線",
  connecting: "連線中",
  unavailable: "暫時無法使用",
  paused: "分頁在背景已暫停",
  disabled: "即時報價未啟用",
};

/** Slim status bar: live-quote connection, Taiwan market session and a Taipei clock. */
export function TopStrip({ live }: { live: LiveState }) {
  const { signalData } = useDashboard();
  const now = useNow(1000);
  const status = live.enabled ? live.status : "disabled";
  const session = now ? marketSession(now) : null;
  const market = signalData.marketEnvironment;
  return (
    <div className="terminal-strip" data-live={status}>
      <span className="strip-pill" data-state={status}>
        <span className="strip-dot" aria-hidden="true" />
        {STATUS_LABEL[status]}
      </span>
      <span className="strip-session" data-state={session ?? "none"}>
        {session ? SESSION_LABEL[session] : " "}
      </span>
      {market && (
        <span className={`strip-regime tone-${regimeTone[market.regime]}`} title={`大盤環境評分 ${market.score.toFixed(0)}（${market.as_of}）`}>
          大盤 {regimeGloss[market.regime]}
          <span className="strip-regime-en"> {regimeCopy[market.regime]}</span>
          <span className="strip-regime-score"> {market.score.toFixed(0)}</span>
        </span>
      )}
      <span className="strip-clock" aria-label="台北時間" suppressHydrationWarning>
        {now ? taipeiClock(now) : ""}
        <span className="strip-tz"> 台北</span>
      </span>
    </div>
  );
}
