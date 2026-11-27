"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { ChevronDownIcon, XIcon } from "@/components/icons";

/** The 「模擬」 badge: shown in the page header and on the ticket's confirm step. */
export function SimBadge({ className = "" }: { className?: string }) {
  return (
    <span className={`sim-badge ${className}`.trim()} title="模擬交易不會送出真實委託">
      模擬
    </span>
  );
}

export const SIM_DISCLAIMER = "模擬交易不會送出真實委託";

/**
 * Modal built on the native <dialog>: focus containment, Escape, backdrop and focus return
 * come from the browser. Children mount only while open, so forms start fresh each time.
 */
export function PaperDialog({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`paper-dialog${wide ? " wide" : ""}`}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open && (
        <div className="paper-dialog-body">
          <header className="paper-dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="icon-button small" onClick={onClose} aria-label="關閉">
              <XIcon size={16} />
            </button>
          </header>
          {children}
        </div>
      )}
    </dialog>
  );
}

export type MenuItem = {
  key: string;
  label: string;
  onSelect: () => void;
  danger?: boolean;
  /** Small trailing text, e.g. the current value. */
  note?: string;
  selected?: boolean;
};

/** Button + popup list (ARIA menu button): arrows move, Escape closes and returns focus. */
export function PaperMenu({
  label,
  trigger,
  items,
  align = "start",
  className = "",
}: {
  /** Accessible name of the trigger. */
  label: string;
  trigger: ReactNode;
  items: (MenuItem | "separator")[];
  align?: "start" | "end";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    rootRef.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) return;
    const entries = [...(rootRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])];
    const index = entries.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      entries[(index + step + entries.length) % entries.length]?.focus();
    } else if (event.key === "Tab") setOpen(false);
  };

  return (
    <div ref={rootRef} className={`paper-menu ${className}`.trim()} onKeyDown={onKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        className="paper-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={label}
        onClick={() => setOpen((value) => !value)}
      >
        {trigger}
        <ChevronDownIcon size={14} />
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label={label} className="paper-menu-list" data-align={align}>
          {items.map((item, index) =>
            item === "separator" ? (
              <hr key={`sep-${index}`} role="separator" />
            ) : (
              <button
                key={item.key}
                type="button"
                role={item.selected !== undefined ? "menuitemradio" : "menuitem"}
                aria-checked={item.selected}
                className={item.danger ? "danger" : undefined}
                onClick={() => {
                  setOpen(false);
                  buttonRef.current?.focus();
                  item.onSelect();
                }}
              >
                <span>{item.label}</span>
                {item.note && <small>{item.note}</small>}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}

/** A labelled figure with tabular numbers (header strip, estimate block). */
export function Figure({
  label,
  children,
  sub,
  className = "",
}: {
  label: string;
  children: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`paper-figure ${className}`.trim()}>
      <dt>{label}</dt>
      <dd>
        <span className="paper-figure-value num">{children}</span>
        {sub && <span className="paper-figure-sub num">{sub}</span>}
      </dd>
    </div>
  );
}
