import type { Alert, Judgement, MarketEnvironment, Regime, Signal, SignalAction } from "./api/types";

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

/** Short Chinese gloss shown next to the English regime name. */
export const regimeGloss: Record<Regime, string> = {
  risk_on: "風險偏好",
  neutral: "中性",
  risk_off: "風險趨避",
};

/** Direction tone (Taiwan convention: up = red, down = green, flat = neutral). */
export type Tone = "up" | "down" | "flat" | "warn";

export const regimeTone: Record<Regime, Tone> = {
  risk_on: "up",
  neutral: "flat",
  risk_off: "down",
};

export const actionTone: Record<SignalAction, Tone> = {
  increase: "up",
  hold: "flat",
  reduce: "down",
  avoid: "down",
};

export const stanceCopy: Record<Judgement["stance"], { label: string; tone: Tone }> = {
  bullish: { label: "偏多", tone: "up" },
  neutral: { label: "中性", tone: "flat" },
  cautious: { label: "謹慎", tone: "warn" },
  bearish: { label: "偏空", tone: "down" },
};

export const severityCopy: Record<Alert["severity"], string> = {
  high: "高",
  medium: "中",
  info: "資訊",
};

/** `?login_error=<code>` returned by the API after a failed Google sign-in. */
export const loginErrorCopy: Record<string, string> = {
  access_denied: "你在 Google 取消了登入。需要時可以再試一次。",
  state: "登入逾時或連線已失效，請重新按一次「使用 Google 帳戶登入」。",
  failed: "登入沒有完成，請再試一次。",
  token_exchange_failed: "與 Google 確認身分時發生問題，請稍後再試一次。",
  google_unreachable: "目前連不上 Google，請稍後再試一次。",
  invalid_token: "Google 回傳的登入資料無法驗證，請再試一次。",
  email_unverified: "這個 Google 帳戶的 Email 尚未通過驗證，請先在 Google 完成驗證，或改用其他帳戶。",
  email_not_allowed: "這個帳戶沒有使用此控制台的權限。若你應該有權限，請聯絡管理員，或改用其他 Google 帳戶。",
  disabled: "這個帳戶目前已停用，請聯絡管理員。",
};

export const loginErrorFallback = loginErrorCopy.failed;
