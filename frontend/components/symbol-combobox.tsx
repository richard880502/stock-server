"use client";

import {
  type KeyboardEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { searchInstruments } from "@/lib/api/endpoints";
import type { Instrument } from "@/lib/api/types";
import type { RecentSymbol } from "@/lib/recent-symbols";
import { normalizeSymbolInput } from "@/lib/symbols";
import { HistoryIcon, SearchIcon } from "./icons";

type Option = {
  key: string;
  symbol: string;
  name?: string;
  market?: Instrument["market"];
  kind: "recent" | "result" | "typed";
};

type SearchState = {
  query: string;
  status: "loading" | "ready" | "error";
  rows: Instrument[];
};

const marketLabel = { TW: "上市", TWO: "上櫃" } as const;

/**
 * Symbol search combobox (ARIA 1.2 pattern). Typing queries
 * GET /api/v1/instruments/search; an empty / untouched field lists recent
 * symbols; a well-formed code that the search does not know can still be
 * submitted (the backend syncs unknown symbols on demand). Press "/" anywhere
 * to focus it.
 */
export function SymbolCombobox({
  value,
  recents,
  onSelect,
}: {
  /** The committed symbol shown when the field is not being edited. */
  value: string;
  recents: RecentSymbol[];
  onSelect: (symbol: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [search, setSearch] = useState<SearchState | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  // Keep the field in step when the committed symbol changes from elsewhere.
  const [syncedValue, setSyncedValue] = useState(value);
  if (syncedValue !== value) {
    setSyncedValue(value);
    setText(value);
  }

  const query = text.trim();
  const pristine = query === "" || query.toUpperCase() === value.toUpperCase();

  // Debounced remote search.
  useEffect(() => {
    if (!open || pristine) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearch((current) => ({ query, status: "loading", rows: current?.rows ?? [] }));
      searchInstruments(query, { signal: controller.signal })
        .then((rows) => setSearch({ query, status: "ready", rows }))
        .catch(() => {
          if (!controller.signal.aborted) setSearch({ query, status: "error", rows: [] });
        });
    }, 160);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, pristine, query]);

  // "/" focuses the field from anywhere outside a text input.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
      ) {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const typedSymbol = pristine ? null : normalizeSymbolInput(query);

  const options = useMemo<Option[]>(() => {
    if (pristine) {
      return recents.map((item) => ({
        key: `recent:${item.symbol}`,
        symbol: item.symbol,
        name: item.name,
        kind: "recent" as const,
      }));
    }
    // While a newer query is in flight, keep only earlier rows that still match it.
    const needle = query.toUpperCase();
    const rows =
      search?.query === query
        ? search.rows
        : (search?.rows ?? []).filter(
            (row) => row.symbol.includes(needle) || row.name.toUpperCase().includes(needle),
          );
    const list: Option[] = rows.map((row) => ({
      key: `result:${row.symbol}`,
      symbol: row.symbol,
      name: row.name,
      market: row.market,
      kind: "result" as const,
    }));
    if (typedSymbol && !rows.some((row) => row.symbol === typedSymbol)) {
      list.push({ key: `typed:${typedSymbol}`, symbol: typedSymbol, kind: "typed" });
    }
    return list;
  }, [pristine, recents, search, typedSymbol, query]);

  const searching = !pristine && (search?.query !== query || search.status === "loading");
  const activeIndex = Math.min(active, options.length - 1);

  const commit = (symbol: string) => {
    setOpen(false);
    setHint(null);
    setActive(-1);
    setText(symbol);
    onSelect(symbol);
    inputRef.current?.blur();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        if (options.length === 0) return;
        const last = options.length - 1;
        setActive((current) =>
          event.key === "ArrowDown"
            ? current >= last ? 0 : current + 1
            : current <= 0 ? last : current - 1,
        );
        break;
      }
      case "Enter": {
        event.preventDefault();
        if (open && activeIndex >= 0 && options[activeIndex]) {
          commit(options[activeIndex].symbol);
          return;
        }
        const normalized = normalizeSymbolInput(query);
        if (normalized) commit(normalized);
        else setHint("請輸入股票代號（例如 2330、6488.TWO）或從清單選擇名稱。");
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        } else {
          setText(value);
          inputRef.current?.blur();
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  const showPanel = open && (options.length > 0 || !pristine || recents.length === 0);
  const activeId = activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined;

  return (
    <div
      className="combobox"
      ref={rootRef}
      onBlur={(event) => {
        if (!rootRef.current?.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
          setHint(null);
          setText(value);
        }
      }}
    >
      <label className="field-label" htmlFor={`${listId}-input`}>
        分析標的
      </label>
      <div className="combobox-control">
        <SearchIcon className="combobox-icon" />
        <input
          id={`${listId}-input`}
          ref={inputRef}
          role="combobox"
          aria-expanded={showPanel}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showPanel ? activeId : undefined}
          aria-describedby={hint ? `${listId}-hint` : undefined}
          value={text}
          placeholder="代號或名稱，例如 2330、台積電"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          enterKeyHint="search"
          onChange={(event) => {
            setText(event.target.value);
            setOpen(true);
            setActive(-1);
            setHint(null);
          }}
          onFocus={(event) => {
            event.currentTarget.select();
            setOpen(true);
          }}
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <kbd className="combobox-kbd" aria-hidden="true">
          /
        </kbd>
      </div>
      {hint && (
        <p className="field-hint" id={`${listId}-hint`} role="alert">
          {hint}
        </p>
      )}
      {showPanel && (
        <div className="combobox-panel" onPointerDown={(event) => event.preventDefault()}>
          {pristine && recents.length > 0 && (
            <p className="combobox-group">
              <HistoryIcon size={14} /> 最近查詢
            </p>
          )}
          <ul role="listbox" id={listId} aria-label="符合的標的">
            {options.map((option, index) => (
              <li
                key={option.key}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className="combobox-option"
                data-active={index === activeIndex || undefined}
                onMouseMove={() => setActive(index)}
                onClick={() => commit(option.symbol)}
              >
                <span className="combobox-symbol mono">{option.symbol}</span>
                <span className="combobox-name">
                  {option.kind === "typed"
                    ? "直接查詢此代號（若資料庫尚無，會先向交易所同步）"
                    : option.name || "—"}
                </span>
                {option.market && (
                  <span className="combobox-market">{marketLabel[option.market]}</span>
                )}
              </li>
            ))}
          </ul>
          {options.length === 0 && (
            <p className="combobox-empty" role="status">
              {pristine
                ? "輸入代號或名稱開始搜尋。"
                : searching
                  ? "搜尋中…"
                  : search?.status === "error"
                    ? "搜尋服務暫時無法使用，仍可直接輸入代號後按 Enter。"
                    : "找不到符合的標的。上市／上櫃代號可直接輸入，例如 2330 或 6488.TWO。"}
            </p>
          )}
          {options.length > 0 && searching && (
            <p className="combobox-empty" role="status">
              搜尋中…
            </p>
          )}
        </div>
      )}
    </div>
  );
}
