"use client";

import { usePathname, useRouter } from "next/navigation";
import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { searchInstruments } from "@/lib/api/endpoints";
import type { Instrument } from "@/lib/api/types";
import { useDashboard } from "@/lib/dashboard-context";
import { applyThemePreference, getThemePreference, THEME_ORDER } from "@/lib/theme";
import { useWatchlist } from "@/lib/use-watchlist";
import { ChartIcon, SearchIcon, StarIcon } from "./icons";
import { VIEWS } from "./view-tabs";

type Command = {
  id: string;
  group: "股票" | "前往" | "動作";
  label: string;
  hint?: string;
  run: () => void;
};

const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/** Opens the palette from anywhere: ⌘K on macOS, Ctrl+K elsewhere. */
export function useCommandPaletteShortcut(open: () => void) {
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey) && !event.altKey) {
        event.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
}

export function CommandPaletteButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className="palette-trigger" onClick={onOpen} aria-label="搜尋股票、頁面與動作（⌘K）">
      <SearchIcon size={16} />
      <span className="palette-trigger-label">搜尋</span>
      <kbd className="palette-kbd" suppressHydrationWarning>{isMac() ? "⌘K" : "Ctrl K"}</kbd>
    </button>
  );
}

/**
 * Global command palette: jump to any stock (search, recents, watchlist), open a page,
 * or run an action. Native <dialog> gives focus containment, Esc and the backdrop.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const { refresh, recentSymbols, query } = useDashboard();
  const watchlist = useWatchlist();
  const [text, setText] = useState("");
  const [results, setResults] = useState<{ q: string; rows: Instrument[] }>({ q: "", rows: [] });
  const [active, setActive] = useState(0);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      inputRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const q = text.trim();
  useEffect(() => {
    if (!open || q.length === 0) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchInstruments(q, { signal: controller.signal })
        .then((rows) => setResults({ q, rows: rows.slice(0, 8) }))
        .catch(() => undefined);
    }, 140);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, q]);

  const close = () => {
    setText("");
    setActive(0);
    onClose();
  };

  const commands = useMemo<Command[]>(() => {
    const openStock = (symbol: string) => {
      refresh({ symbol });
      if (pathname.replace(/\/+$/, "") !== "") router.push("/#stock-signal");
      else window.setTimeout(() => document.getElementById("stock-signal")?.scrollIntoView({ behavior: "smooth" }), 50);
    };
    const backtest = (symbol: string) => {
      refresh({ symbol });
      router.push("/backtest");
    };
    const needle = q.toLowerCase();
    const matches = (label: string) => needle === "" || label.toLowerCase().includes(needle);

    const stocks: Command[] = [];
    const seen = new Set<string>();
    const addStock = (symbol: string, name: string | undefined, hint: string) => {
      const key = symbol.toUpperCase();
      if (seen.has(key)) return;
      seen.add(key);
      stocks.push({ id: `stock:${key}`, group: "股票", label: `${key}${name ? ` ${name}` : ""}`, hint, run: () => openStock(key) });
    };
    if (q && results.q === q) results.rows.forEach((row) => addStock(row.symbol, row.name, row.market === "TW" ? "上市" : "上櫃"));
    if (!q) {
      recentSymbols.slice(0, 5).forEach((r) => addStock(r.symbol, r.name, "最近查詢"));
      watchlist.watchlist?.items.slice(0, 8).forEach((item) => addStock(item.symbol, item.name, "自選股"));
    }
    // A well-formed code typed directly can be opened even before search answers.
    if (/^[0-9A-Z^][0-9A-Z.^]{1,15}$/i.test(q) && !seen.has(q.toUpperCase())) addStock(q, undefined, "直接開啟");

    const pages: Command[] = VIEWS.filter((v) => matches(v.label)).map((v) => ({
      id: `page:${v.href}`,
      group: "前往",
      label: v.label,
      run: () => router.push(v.href),
    }));

    const current = query.symbol;
    const actions: Command[] = [
      { id: "act:backtest", group: "動作" as const, label: `回測 ${current}`, run: () => backtest(current) },
      {
        id: "act:watch",
        group: "動作" as const,
        label: watchlist.has(current) ? `將 ${current} 移出自選股` : `將 ${current} 加入自選股`,
        run: () => void watchlist.setMember(current, !watchlist.has(current)),
      },
      {
        id: "act:theme",
        group: "動作" as const,
        label: "切換主題（跟隨系統 → 淺色 → 深色）",
        run: () => {
          const now = getThemePreference();
          applyThemePreference(THEME_ORDER[(THEME_ORDER.indexOf(now) + 1) % THEME_ORDER.length]);
        },
      },
    ].filter((a) => matches(a.label));

    return [...stocks, ...pages, ...actions];
  }, [q, results, recentSymbols, watchlist, query.symbol, refresh, router, pathname]);

  const activeIndex = Math.min(active, Math.max(0, commands.length - 1));
  const choose = (command: Command | undefined) => {
    if (!command) return;
    close();
    command.run();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((activeIndex + 1) % Math.max(1, commands.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((activeIndex - 1 + commands.length) % Math.max(1, commands.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(commands[activeIndex]);
    }
  };

  useEffect(() => {
    document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, listId]);

  let lastGroup: Command["group"] | null = null;
  return (
    <dialog
      ref={dialogRef}
      className="palette"
      aria-label="快速搜尋"
      onClose={close}
      onClick={(event) => {
        if (event.target === dialogRef.current) close();
      }}
    >
      <div className="palette-field">
        <SearchIcon size={18} />
        <input
          ref={inputRef}
          className="palette-input"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={commands.length ? `${listId}-${activeIndex}` : undefined}
          aria-autocomplete="list"
          placeholder="輸入代號或名稱，例如 2330、台積電、回測…"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          autoComplete="off"
          spellCheck={false}
        />
        <kbd className="palette-kbd">Esc</kbd>
      </div>
      <ul id={listId} className="palette-list" role="listbox" aria-label="結果">
        {commands.length === 0 && <li className="palette-empty">沒有符合的結果</li>}
        {commands.map((command, index) => {
          const heading = command.group !== lastGroup ? command.group : null;
          lastGroup = command.group;
          return (
            <li key={command.id} role="presentation">
              {heading && <div className="palette-group" aria-hidden="true">{heading}</div>}
              <div
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className="palette-option"
                onMouseMove={() => setActive(index)}
                onClick={() => choose(command)}
              >
                <span className="palette-option-icon" aria-hidden="true">
                  {command.group === "股票" ? (
                    command.hint === "自選股" ? <StarIcon size={15} filled /> : <SearchIcon size={15} />
                  ) : (
                    <ChartIcon size={15} />
                  )}
                </span>
                <span className="palette-option-label">{command.label}</span>
                {command.hint && <span className="palette-option-hint">{command.hint}</span>}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="palette-footer">↑↓ 選擇 · Enter 開啟 · Esc 關閉</p>
    </dialog>
  );
}
