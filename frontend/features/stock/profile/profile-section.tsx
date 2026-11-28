"use client";

import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/card";
import { getStockFactors, type StockFactors } from "@/lib/api/profile";
import { EarningsTab } from "./earnings-tab";
import { EventsTab } from "./events-tab";
import { FuturesTab } from "./futures-tab";
import { HoldersTab } from "./holders-tab";
import { KeyFacts, KeyFactsSkeleton } from "./key-facts";
import { DEFAULT_TAB, nextTab, parseTabHash, PROFILE_TABS, type ProfileTab, tabHash } from "./model";
import { PanelError } from "./parts";
import "./profile.css";
import { ShortTab } from "./short-tab";
import { profileKey, useProfileResource } from "./use-profile-resource";
import { ValuationTab } from "./valuation-tab";

/**
 * 「個股全貌」: key facts from the screener row, then six lazily loaded data tabs. The open tab
 * lives in the URL hash (`#profile=earnings`) so a link or a reload keeps it.
 */
export function StockProfileSection({ symbol, asOf }: { symbol: string; asOf: string }) {
  // Server render and first client render both show the default tab; the hash is applied after mount.
  const [tab, setTab] = useState<ProfileTab>(DEFAULT_TAB);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const apply = () => {
      const fromHash = parseTabHash(window.location.hash);
      if (fromHash) setTab(fromHash);
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, []);

  const select = useCallback((next: ProfileTab) => {
    setTab(next);
    try {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${tabHash(next)}`);
    } catch {
      // History access can be blocked in sandboxed previews; the tab still switches.
    }
  }, []);

  const onKeyDown = (event: KeyboardEvent) => {
    const next = nextTab(tab, event.key);
    if (!next) return;
    event.preventDefault();
    select(next);
    listRef.current?.querySelector<HTMLButtonElement>(`#profile-tab-${next}`)?.focus();
  };

  const factors = useProfileResource<StockFactors>(profileKey("factors", symbol, asOf), (signal) =>
    getStockFactors(symbol, asOf, { signal }),
  );
  const close = factors.data?.entry?.close ?? null;

  return (
    <Card
      title="個股全貌"
      sub="估值、營收獲利、大戶與外資、放空與當沖、期貨與事件；皆為官方公布的計算事實，各區標示資料日。"
      className="pf-card"
      aria-label="個股全貌"
    >
      {factors.data ? (
        <KeyFacts factors={factors.data} />
      ) : factors.error ? (
        <PanelError message={factors.error} onRetry={factors.retry} />
      ) : (
        <KeyFactsSkeleton />
      )}

      <div className="pf-tabs" role="tablist" aria-label="個股全貌分頁" ref={listRef} onKeyDown={onKeyDown}>
        {PROFILE_TABS.map((item) => (
          <button
            key={item.id}
            id={`profile-tab-${item.id}`}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            aria-controls={`profile-panel-${item.id}`}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => select(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        className="pf-panel"
        role="tabpanel"
        id={`profile-panel-${tab}`}
        aria-labelledby={`profile-tab-${tab}`}
        tabIndex={0}
      >
        <ActiveTab tab={tab} symbol={symbol} asOf={asOf} close={close} />
      </div>
    </Card>
  );
}

export function ActiveTab({ tab, symbol, asOf, close }: { tab: ProfileTab; symbol: string; asOf: string; close: number | null }) {
  switch (tab) {
    case "valuation":
      return <ValuationTab key={symbol} symbol={symbol} asOf={asOf} />;
    case "earnings":
      return <EarningsTab key={symbol} symbol={symbol} asOf={asOf} />;
    case "holders":
      return <HoldersTab key={symbol} symbol={symbol} asOf={asOf} close={close} />;
    case "short":
      return <ShortTab key={symbol} symbol={symbol} asOf={asOf} />;
    case "futures":
      return <FuturesTab key={symbol} symbol={symbol} asOf={asOf} />;
    case "events":
      return <EventsTab key={symbol} symbol={symbol} asOf={asOf} />;
  }
}
