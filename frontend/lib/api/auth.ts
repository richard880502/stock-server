// API key storage. The key comes from POST /api/v1/auth/login, lives in
// localStorage and is attached as `Authorization: Bearer` by withAuthHeaders().

const AUTH_STORAGE_KEY = "quant_signal_api_key";

let cachedKey: string | null | undefined;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  if (cachedKey === undefined) {
    try {
      cachedKey = window.localStorage.getItem(AUTH_STORAGE_KEY);
    } catch {
      cachedKey = null;
    }
  }
  return cachedKey;
}

export function saveApiKey(key: string): void {
  cachedKey = key;
  try {
    window.localStorage.setItem(AUTH_STORAGE_KEY, key);
  } catch {
    // Storage unavailable (private mode): the key still works for this page load.
  }
  emit();
}

export function clearApiKey(): void {
  cachedKey = null;
  try {
    window.localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // ignore
  }
  emit();
}

/** Subscribe to key changes (login, logout, or another tab). */
export function subscribeApiKey(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === AUTH_STORAGE_KEY || event.key === null) {
      cachedKey = undefined;
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function withAuthHeaders(init?: RequestInit): RequestInit {
  const key = getApiKey();
  if (!key) return init ?? {};
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${key}`);
  return { ...init, headers };
}
