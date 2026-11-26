export type WatchView = "terminal" | "list";

export const VIEW_KEY = "quant_signal_watch_view";

export function parseView(value: string | null | undefined): WatchView | null {
  return value === "terminal" || value === "list" ? value : null;
}

/** Which view to show: the viewer's click this visit, then ?view=, then the saved choice, then the screen size. */
export function resolveView(opts: {
  clicked: WatchView | null;
  param: string | null;
  stored: string | null;
  narrow: boolean;
}): WatchView {
  return opts.clicked ?? parseView(opts.param) ?? parseView(opts.stored) ?? (opts.narrow ? "list" : "terminal");
}
