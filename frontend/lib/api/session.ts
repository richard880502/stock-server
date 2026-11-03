// Sign-in state. Two methods run side by side:
//  - Google: the API sets an HttpOnly `qs_session` cookie; JS only learns who is
//    signed in from GET /auth/me.
//  - Password (legacy): an API key in localStorage (see auth.ts).
import { useSyncExternalStore } from "react";
import { clearApiKey, getApiKey } from "./auth";
import { apiUrl, onUnauthorized } from "./client";

export type AuthConfig = { google: boolean; password: boolean };
export type SessionUser = { id: string; email: string; name: string | null; picture: string | null };

export type SessionState = {
  /** `loading` until /auth/config (and /auth/me when needed) have answered. */
  phase: "loading" | "ready";
  config: AuthConfig | null;
  user: SessionUser | null;
  /** Set when the session ended on its own (HTTP 401 while signed in). */
  expired: boolean;
  /** Code from `?login_error=` after a failed Google round trip. */
  loginError: string | null;
};

const LOADING: SessionState = {
  phase: "loading",
  config: null,
  user: null,
  expired: false,
  loginError: null,
};

let state = LOADING;
const listeners = new Set<() => void>();

function setState(patch: Partial<SessionState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function useSession(): SessionState {
  return useSyncExternalStore(subscribe, () => state, () => LOADING);
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(apiUrl(path), { credentials: "include" });
    return response.ok ? ((await response.json()) as T) : null;
  } catch {
    return null;
  }
}

/** Take `?login_error=` out of the URL so a reload does not repeat the message. */
function consumeLoginError(): string | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("login_error");
  if (code === null) return null;
  url.searchParams.delete("login_error");
  try {
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch {
    // history unavailable: the message just shows again on reload
  }
  return code || "failed";
}

let started = false;

/** Resolve config + session once per page load. */
export async function startSession(): Promise<void> {
  if (started) return;
  started = true;
  const loginError = consumeLoginError();
  const config = (await getJson<AuthConfig>("/api/v1/auth/config")) ?? {
    // Older API without the endpoint: behave like the password-only dashboard.
    google: false,
    password: true,
  };
  // A stored password key means the user is already in; skip the cookie lookup.
  const user =
    config.google && !getApiKey() && !loginError
      ? await getJson<SessionUser>("/api/v1/auth/me")
      : null;
  setState({ phase: "ready", config, user, loginError });
}

/** Sign out of whichever method is active and return to the login screen. */
export async function signOut(): Promise<void> {
  if (state.user) {
    try {
      await fetch(apiUrl("/api/v1/auth/logout"), { method: "POST", credentials: "include" });
    } catch {
      // Offline: still drop the client state; the cookie expires on its own.
    }
  }
  clearApiKey();
  setState({ user: null, expired: false, loginError: null });
}

/** Called by the API client on HTTP 401: the session or key is no longer valid. */
function handleUnauthorized() {
  if (!state.user && !getApiKey()) return;
  clearApiKey();
  setState({ user: null, expired: true });
}
onUnauthorized(handleUnauthorized);

export function dismissSessionNotes(): void {
  if (state.expired || state.loginError) setState({ expired: false, loginError: null });
}
