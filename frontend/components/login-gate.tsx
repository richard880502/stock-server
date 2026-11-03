"use client";

import { FormEvent, type ReactNode, useState, useSyncExternalStore } from "react";
import { getApiKey, saveApiKey, subscribeApiKey } from "@/lib/api/auth";
import { login } from "@/lib/api/endpoints";
import { BrandMark } from "./brand";
import { ThemeToggle } from "./theme-toggle";

const subscribeNever = () => () => {};

/** Renders children only while an API key is stored; otherwise the password form. */
export function LoginGate({ children }: { children: ReactNode }) {
  const apiKey = useSyncExternalStore(subscribeApiKey, getApiKey, () => null);
  // The stored key is only readable in the browser; until hydration finishes,
  // show a neutral splash instead of flashing the login form at signed-in users.
  const hydrated = useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false,
  );
  if (!hydrated) return <div className="splash" aria-hidden="true" />;
  if (!apiKey) return <LoginScreen />;
  return <>{children}</>;
}

function LoginScreen() {
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoggingIn(true);
    setLoginError(null);
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

  return (
    <main className="login-screen">
      <div className="login-theme">
        <ThemeToggle />
      </div>
      <form className="login-card" onSubmit={handleLogin}>
        <BrandMark size={44} />
        <div className="login-copy">
          <h1>Quant Signal</h1>
          <p id="login-hint">輸入密碼以進入研究控制台。</p>
        </div>
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
            autoFocus
          />
        </div>
        {loginError && (
          <p className="inline-error" id="login-error" role="alert">
            {loginError}
          </p>
        )}
        <button className="button primary block" disabled={loggingIn || !password}>
          {loggingIn ? "登入中…" : "登入"}
        </button>
        <p className="login-foot">研究工具，不構成投資建議。</p>
      </form>
    </main>
  );
}
