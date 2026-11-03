import type { MarketEnvironment, Regime, Signal, SignalAction } from "./api/types";

export const factorLabels: Record<keyof Signal["factors"], string> = {
  trend: "趨勢結構",
  momentum: "價格動能",
  volume: "量能確認",
  relative_strength: "相對強弱",
  risk_quality: "風險品質",
};

export const marketFactorLabels: Record<keyof MarketEnvironment["factors"], string> = {
  participation: "均線參與度",
  advance_decline: "漲跌家數",
  breadth_momentum: "創高創低",
  volume_breadth: "量能廣度",
  institutional_flow: "法人資金環境",
  leverage_quality: "槓桿品質",
};

export const actionCopy: Record<SignalAction, { label: string; guide: string }> = {
  increase: {
    label: "偏多配置",
    guide: "訊號支持偏多，但仍應等待價格確認並依風險承受度調整曝險。",
  },
  hold: {
    label: "維持觀察",
    guide: "多空證據尚未形成壓倒性方向，保留部位並等待條件確認。",
  },
  reduce: {
    label: "降低風險",
    guide: "環境轉弱，優先控制曝險，避免在波動擴張時增加部位。",
  },
  avoid: {
    label: "暫停進場",
    guide: "目前風險報酬不具優勢，等待訊號回到可接受區間。",
  },
};

export const regimeCopy: Record<Regime, string> = {
  risk_on: "Risk-on",
  neutral: "Neutral",
  risk_off: "Risk-off",
};
