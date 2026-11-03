"use client";

import { FormEvent, type ReactNode, useState, useSyncExternalStore } from "react";
import { getApiKey, saveApiKey, subscribeApiKey } from "@/lib/api/auth";
import { login } from "@/lib/api/endpoints";

/** Renders children only while an API key is stored; otherwise the password form. */
export function LoginGate({ children }: { children: ReactNode }) {
  const apiKey = useSyncExternalStore(subscribeApiKey, getApiKey, () => null);
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
      <form className="login-card" onSubmit={handleLogin}>
        <span className="brand-mark" aria-hidden="true">
          QS
        </span>
        <h1>Quant Signal</h1>
        <p id="login-hint">輸入密碼以進入研究控制台。</p>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="密碼"
          aria-label="密碼"
          aria-describedby={loginError ? "login-error" : "login-hint"}
          aria-invalid={loginError ? true : undefined}
          autoComplete="current-password"
          autoFocus
        />
        {loginError && (
          <span className="login-error" id="login-error" role="alert">
            {loginError}
          </span>
        )}
        <button className="primary-button" disabled={loggingIn || !password}>
          {loggingIn ? "登入中…" : "登入"}
        </button>
      </form>
    </main>
  );
}
