export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_partial: boolean;
  lastTradeTime: number;
};

/** Merge observations of the same minute without losing newer or wider data. */
export function mergeBars(history: Bar[], live: Bar[]): Bar[] {
  const merged = new Map(history.map((bar) => [bar.time, bar]));
  for (const current of live) {
    const prior = merged.get(current.time);
    if (!prior) {
      merged.set(current.time, current);
      continue;
    }
    merged.set(current.time, {
      ...prior,
      high: Math.max(prior.high, current.high),
      low: Math.min(prior.low, current.low),
      volume: Math.max(prior.volume, current.volume),
      close:
        current.lastTradeTime >= prior.lastTradeTime
          ? current.close
          : prior.close,
      lastTradeTime: Math.max(prior.lastTradeTime, current.lastTradeTime),
      is_partial: prior.is_partial || current.is_partial,
    });
  }
  return [...merged.values()].sort((a, b) => a.time - b.time);
}

export function aggregateBars(bars: Bar[], period: number): Bar[] {
  if (period === 1) return bars;
  const result: Bar[] = [];
  for (const bar of bars) {
    const time = Math.floor(bar.time / 300) * 300;
    const last = result[result.length - 1];
    if (last?.time === time) {
      last.high = Math.max(last.high, bar.high);
      last.low = Math.min(last.low, bar.low);
      last.close = bar.close;
      last.volume += bar.volume;
      last.is_partial ||= bar.is_partial;
      last.lastTradeTime = bar.lastTradeTime;
    } else {
      result.push({ ...bar, time });
    }
  }
  return result;
}
