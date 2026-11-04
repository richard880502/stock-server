"use client";

import type { ReactNode } from "react";
import { DashboardProvider } from "@/lib/dashboard-context";
import { WatchlistProvider } from "@/lib/use-watchlist";
import { DashboardFrame } from "./dashboard-frame";
import { LoginGate } from "./login-gate";

/**
 * Mounted once in the root layout so login state, the shared symbol / as-of
 * date and in-flight results survive navigation between routes.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <LoginGate>
      <DashboardProvider>
        <WatchlistProvider>
          <DashboardFrame>{children}</DashboardFrame>
        </WatchlistProvider>
      </DashboardProvider>
    </LoginGate>
  );
}
