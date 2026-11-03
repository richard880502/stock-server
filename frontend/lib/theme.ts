import { writeStorage } from "./safe-storage";

export const THEME_STORAGE_KEY = "quant_signal_theme";

/** "system" follows prefers-color-scheme; the others pin the theme. */
export type ThemePreference = "system" | "light" | "dark";

export const THEME_ORDER: ThemePreference[] = ["system", "light", "dark"];

/** Runs in <head> before first paint so a pinned theme never flashes. */
export const themeInitScript = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})();`;

const listeners = new Set<() => void>();

/** useSyncExternalStore source: the pinned theme lives on <html data-theme>. */
export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getThemePreference(): ThemePreference {
  const pinned = document.documentElement.getAttribute("data-theme");
  return pinned === "light" || pinned === "dark" ? pinned : "system";
}

export const getServerThemePreference = (): ThemePreference => "system";

export function applyThemePreference(preference: ThemePreference): void {
  const root = document.documentElement;
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
  writeStorage(THEME_STORAGE_KEY, preference === "system" ? null : preference);
  listeners.forEach((listener) => listener());
}
