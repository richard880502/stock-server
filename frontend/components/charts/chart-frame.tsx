"use client";

import {
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

export type ChartMargin = { top: number; right: number; bottom: number; left: number };

export type ChartCtx = {
  width: number;
  height: number;
  margin: ChartMargin;
  /** Plot-area bounds in SVG pixels. */
  plot: { left: number; right: number; top: number; bottom: number };
};

export type Readout = {
  /** Heading line, usually the date. */
  title: string;
  rows: { label: string; value: string; /** CSS colour of the line key */ color: string; dashed?: boolean }[];
  /** Optional muted footnote line. */
  note?: string;
};

/**
 * Shared interactive shell for the SVG charts: measures its container, tracks
 * the pointer / arrow keys to a data index, draws the crosshair and renders an
 * HTML tooltip. The chart supplies the geometry (`xAt`), the drawing
 * (`children`, `markers`) and the readout text per index.
 */
export function ChartFrame({
  count,
  xAt,
  label,
  hint = "可用左右方向鍵逐一檢視數值",
  margin = { top: 16, right: 16, bottom: 28, left: 56 },
  heightFor,
  children,
  markers,
  readout,
  defaultIndex,
}: {
  count: number;
  /** x pixel for data index i given the plot bounds. */
  xAt: (index: number, plot: ChartCtx["plot"]) => number;
  label: string;
  hint?: string;
  margin?: ChartMargin;
  heightFor?: (width: number) => number;
  children: (ctx: ChartCtx) => ReactNode;
  /** Dots drawn on the series at the hovered index. */
  markers?: (index: number, ctx: ChartCtx) => ReactNode;
  readout: (index: number) => Readout;
  /** Index announced to assistive tech before any interaction. */
  defaultIndex?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  const hintId = useId();

  useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const height = heightFor ? heightFor(width) : width < 520 ? 230 : 320;
  const plot = {
    left: margin.left,
    right: Math.max(margin.left + 10, width - margin.right),
    top: margin.top,
    bottom: height - margin.bottom,
  };
  const ctx: ChartCtx = { width, height, margin, plot };
  const ready = width > 0 && count > 0;

  const indexFromPointer = (event: PointerEvent<Element>) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || count === 0) return null;
    const px = event.clientX - rect.left;
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i < count; i += 1) {
      const distance = Math.abs(xAt(i, plot) - px);
      if (distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    }
    return best;
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const last = count - 1;
    const current = hover ?? defaultIndex ?? last;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = Math.max(0, current - 1);
    else if (event.key === "ArrowRight") next = Math.min(last, current + 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else if (event.key === "Escape") {
      setHover(null);
      return;
    }
    if (next !== null) {
      event.preventDefault();
      setHover(next);
    }
  };

  const active = hover ?? null;
  const info = ready && active !== null ? readout(active) : null;
  const cursorX = ready && active !== null ? xAt(active, plot) : 0;
  const announce = ready ? readout(hover ?? defaultIndex ?? count - 1) : null;
  // Flip the tooltip to the cursor's left once it is past the middle of the plot.
  const flip = cursorX > width * 0.55;

  return (
    <div
      className="chart"
      ref={wrapRef}
      role="group"
      aria-label={label}
      aria-describedby={hintId}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onFocus={() => setHover((current) => current ?? defaultIndex ?? count - 1)}
      onBlur={() => setHover(null)}
      onPointerMove={(event) => setHover(indexFromPointer(event))}
      onPointerDown={(event) => setHover(indexFromPointer(event))}
      onPointerLeave={(event) => {
        if (event.pointerType !== "touch" && document.activeElement !== wrapRef.current) setHover(null);
      }}
    >
      {ready && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          {children(ctx)}
          {active !== null && (
            <g className="chart-cursor">
              <line x1={cursorX} x2={cursorX} y1={plot.top} y2={plot.bottom} />
              {markers?.(active, ctx)}
            </g>
          )}
        </svg>
      )}
      {!ready && <div className="skeleton block" style={{ height: 280 }} />}
      {info && (
        <div
          className="chart-tooltip"
          style={{
            left: flip ? cursorX - 14 : cursorX + 14,
            transform: flip ? "translateX(-100%)" : undefined,
            top: plot.top + 4,
          }}
        >
          <p className="chart-tooltip-title num">{info.title}</p>
          {info.rows.map((row) => (
            <p className="chart-tooltip-row" key={row.label}>
              <span className="line-key" style={{ color: row.color }} data-dashed={row.dashed || undefined} aria-hidden="true" />
              <span className="chart-tooltip-label">{row.label}</span>
              <strong className="num">{row.value}</strong>
            </p>
          ))}
          {info.note && <p className="chart-tooltip-note">{info.note}</p>}
        </div>
      )}
      <p className="sr-only" id={hintId}>
        {hint}
      </p>
      <p className="sr-only" aria-live="polite">
        {announce && hover !== null ? `${announce.title}：${announce.rows.map((r) => `${r.label} ${r.value}`).join("，")}` : ""}
      </p>
    </div>
  );
}

/** Legend: line key + label per series (identity is never colour alone). */
export function ChartLegend({
  items,
}: {
  items: { label: string; color: string; dashed?: boolean; band?: boolean }[];
}) {
  return (
    <ul className="chart-legend" aria-label="圖例">
      {items.map((item) => (
        <li key={item.label}>
          {item.band ? (
            <span className="band-key" style={{ background: item.color }} aria-hidden="true" />
          ) : (
            <span className="line-key" style={{ color: item.color }} data-dashed={item.dashed || undefined} aria-hidden="true" />
          )}
          {item.label}
        </li>
      ))}
    </ul>
  );
}
