"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";

export type DrawingTool = "cursor" | "trend" | "horizontal" | "range";

type Point = { x: number; y: number };
type Drawing = {
  id: number;
  tool: Exclude<DrawingTool, "cursor">;
  start: Point;
  end: Point;
};

const MARGIN = { top: 14, right: 18, bottom: 28, left: 58 };

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function DrawingLayer({
  tool,
  drawings,
  onChange,
}: {
  tool: DrawingTool;
  drawings: Drawing[];
  onChange: (drawings: Drawing[]) => void;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 0, height: 320 });
  const [draft, setDraft] = useState<Drawing | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const parent = node.parentElement;
    if (!parent) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      setSize({ width, height: width < 520 ? 230 : 320 });
    });
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);

  const pointFromEvent = (event: PointerEvent<SVGSVGElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp(event.clientX - rect.left, MARGIN.left, rect.width - MARGIN.right),
      y: clamp(event.clientY - rect.top, MARGIN.top, rect.height - MARGIN.bottom),
    };
  };

  const startDrawing = (event: PointerEvent<SVGSVGElement>) => {
    if (tool === "cursor") return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointFromEvent(event);
    setDraft({ id: Date.now(), tool, start: point, end: point });
  };

  const moveDrawing = (event: PointerEvent<SVGSVGElement>) => {
    if (!draft) return;
    const point = pointFromEvent(event);
    setDraft((current) =>
      current
        ? {
            ...current,
            end: current.tool === "horizontal" ? { x: point.x, y: current.start.y } : point,
          }
        : null,
    );
  };

  const finishDrawing = (event: PointerEvent<SVGSVGElement>) => {
    if (!draft) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    const distance = Math.hypot(draft.end.x - draft.start.x, draft.end.y - draft.start.y);
    if (draft.tool === "horizontal" || distance > 6) onChange([...drawings, draft]);
    setDraft(null);
  };

  const renderDrawing = (drawing: Drawing) => {
    if (drawing.tool === "range") {
      return (
        <rect
          key={drawing.id}
          className="analysis-range"
          x={Math.min(drawing.start.x, drawing.end.x)}
          y={Math.min(drawing.start.y, drawing.end.y)}
          width={Math.abs(drawing.end.x - drawing.start.x)}
          height={Math.abs(drawing.end.y - drawing.start.y)}
          rx="4"
        />
      );
    }
    return (
      <g key={drawing.id} className="analysis-line">
        <line
          x1={drawing.tool === "horizontal" ? MARGIN.left : drawing.start.x}
          y1={drawing.start.y}
          x2={drawing.tool === "horizontal" ? size.width - MARGIN.right : drawing.end.x}
          y2={drawing.tool === "horizontal" ? drawing.start.y : drawing.end.y}
        />
        {drawing.tool === "trend" && (
          <>
            <circle cx={drawing.start.x} cy={drawing.start.y} r="3" />
            <circle cx={drawing.end.x} cy={drawing.end.y} r="3" />
          </>
        )}
      </g>
    );
  };

  return (
    <svg
      ref={ref}
      className="drawing-layer"
      data-active={tool !== "cursor" || undefined}
      width={size.width}
      height={size.height}
      viewBox={`0 0 ${size.width} ${size.height}`}
      aria-hidden="true"
      onPointerDown={startDrawing}
      onPointerMove={moveDrawing}
      onPointerUp={finishDrawing}
      onPointerCancel={() => setDraft(null)}
    >
      {drawings.map(renderDrawing)}
      {draft && renderDrawing(draft)}
    </svg>
  );
}

export type { Drawing };
