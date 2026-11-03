"use client";

import { useSyncExternalStore } from "react";
import {
  applyThemePreference,
  getServerThemePreference,
  getThemePreference,
  subscribeTheme,
  THEME_ORDER,
  type ThemePreference,
} from "@/lib/theme";
import { MonitorIcon, MoonIcon, SunIcon } from "./icons";

const LABELS: Record<ThemePreference, string> = {
  system: "跟隨系統",
  light: "淺色",
  dark: "深色",
};

const ICONS = { system: MonitorIcon, light: SunIcon, dark: MoonIcon };

/** Cycles 跟隨系統 → 淺色 → 深色; the choice is persisted in localStorage. */
export function ThemeToggle() {
  const preference = useSyncExternalStore(
    subscribeTheme,
    getThemePreference,
    getServerThemePreference,
  );

  const next = THEME_ORDER[(THEME_ORDER.indexOf(preference) + 1) % THEME_ORDER.length];
  const Current = ICONS[preference];

  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      onClick={() => applyThemePreference(next)}
      aria-label={`色彩主題：${LABELS[preference]}，點擊切換為${LABELS[next]}`}
      title={`主題：${LABELS[preference]}`}
    >
      <Current />
      <span className="theme-toggle-label">{LABELS[preference]}</span>
    </button>
  );
}
