"use client";

import { FormEvent, type ReactNode, useEffect, useState, useSyncExternalStore } from "react";
import { getApiKey, saveApiKey, subscribeApiKey } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/client";
import { login } from "@/lib/api/endpoints";
import { dismissSessionNotes, startSession, useSession } from "@/lib/api/session";
import { loginErrorCopy, loginErrorFallback } from "@/lib/copy";
import { BrandMark } from "./brand";
import { AlertIcon, InfoIcon } from "./icons";
import { ThemeToggle } from "./theme-toggle";

const subscribeNever = () => () => {};

/** Renders children only while signed in (Google session or stored API key); otherwise the login screen. */
export function LoginGate({ children }: { children: ReactNode }) {
  const apiKey = useSyncExternalStore(subscribeApiKey, getApiKey, () => null);
  const session = useSession();
  // Credentials are only readable in the browser; until hydration finishes and
  // /auth/config + /auth/me have answered, show a skeleton instead of flashing
  // the login form at signed-in users (or the dashboard at signed-out ones).
  const hydrated = useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false,
  );
  useEffect(() => {
    void startSession();
  }, []);
  if (!hydrated) return <div className="splash" aria-hidden="true" />;
  if (apiKey || session.user) return <>{children}</>;
  if (session.phase === "loading") return <LoginSkeleton />;
  return <LoginScreen />;
}

/** Same footprint as the login card so nothing jumps when the real one appears. */
function LoginSkeleton() {
  return (
    <main className="login-screen" aria-busy="true" aria-label="正在確認登入狀態">
      <div className="login-card" role="status">
        <span className="skeleton block" style={{ width: 44, height: 44 }} />
        <div className="login-copy">
          <span className="skeleton line" style={{ width: "55%", height: "1.5rem" }} />
          <span className="skeleton line" style={{ width: "80%", marginTop: "0.75rem" }} />
        </div>
        <span className="skeleton block" style={{ height: "2.75rem" }} />
        <span className="skeleton line" style={{ width: "50%", margin: "0 auto" }} />
      </div>
    </main>
  );
}

function GoogleLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

/** Full-page navigation: the API redirects to Google and back, JS never sees the token. */
function googleLoginUrl(): string {
  const next = window.location.pathname + window.location.search;
  return `${apiUrl("/api/v1/auth/google/login")}?next=${encodeURIComponent(next)}`;
}

function LoginScreen() {
  const { config, expired, loginError: loginCode } = useSession();
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [redirecting, setRedirecting] = useState(false);

  // Returning with the back button restores this page from cache mid-redirect.
  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setRedirecting(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  const showGoogle = config?.google === true;
  const showPassword = config?.password === true;
  const note = expired
    ? { tone: "info", text: "登入已過期，請重新登入。" }
    : loginCode
      ? { tone: "warn", text: loginErrorCopy[loginCode] ?? loginErrorFallback }
      : null;

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoggingIn(true);
    setLoginError(null);
    dismissSessionNotes();
    try {
      const { api_key } = await login(password);
      saveApiKey(api_key);
      setPassword("");
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "登入失敗");
    } finally {
      setLoggingIn(false);
    }
  };

  const hint = showGoogle
    ? "使用 Google 帳戶進入研究控制台。"
    : showPassword
      ? "輸入密碼以進入研究控制台。"
      : "目前沒有可用的登入方式。";

  return (
    <main className="login-screen">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <div className="login-card">
        <BrandMark size={44} />
        <div className="login-copy">
          <h1>Quant Signal</h1>
          <p id="login-hint">{hint}</p>
        </div>

        {note && (
          <div
            className="login-note"
            data-tone={note.tone}
            role={note.tone === "info" ? "status" : "alert"}
          >
            {note.tone === "info" ? <InfoIcon size={18} /> : <AlertIcon size={18} />}
            <p>{note.text}</p>
          </div>
        )}

        {showGoogle && (
          <a
            className="google-button"
            href={googleLoginUrl()}
            aria-disabled={redirecting || undefined}
            onClick={(event) => {
              if (redirecting) event.preventDefault();
              setRedirecting(true);
            }}
          >
            <GoogleLogo />
            <span>{redirecting ? "前往 Google…" : "使用 Google 帳戶登入"}</span>
          </a>
        )}

        {showGoogle && showPassword && (
          <div className="login-divider" role="separator">
            <span>或使用密碼</span>
          </div>
        )}

        {showPassword && (
          <form className="login-form" onSubmit={handleLogin}>
            <div className="login-field">
              <label className="field-label" htmlFor="login-password">
                密碼
              </label>
              <input
                id="login-password"
                className="input"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-describedby={loginError ? "login-error" : "login-hint"}
                aria-invalid={loginError ? true : undefined}
                autoComplete="current-password"
                autoFocus={!showGoogle}
              />
            </div>
            {loginError && (
              <p className="inline-error" id="login-error" role="alert">
                {loginError}
              </p>
            )}
            <button
              className={`button block${showGoogle ? "" : " primary"}`}
              disabled={loggingIn || !password}
            >
              {loggingIn ? "登入中…" : "登入"}
            </button>
          </form>
        )}

        {!showGoogle && !showPassword && (
          <div className="login-note" data-tone="warn" role="alert">
            <AlertIcon size={18} />
            <div>
              <strong>尚未設定登入方式</strong>
              <p>請管理員在服務端設定 Google 登入或儀表板密碼後，再重新整理此頁。</p>
            </div>
          </div>
        )}
        <p className="login-foot">研究工具，不構成投資建議。</p>
      </div>
    </main>
  );
}
