# Quant Signal Dashboard

Responsive web console for the Quant Signal Server.

```bash
cp .env.example .env.local
npm install
npm run dev
```

The default API URL is `http://127.0.0.1:8000`. Configure
`NEXT_PUBLIC_QUANT_API_URL` before building when the API runs elsewhere.

## Layout

- `app/` – routes only: `/` (今日訊號), `/backtest`, `/forecast`, plus the root layout and `globals.css`.
- `components/` – app shell (login gate, top bar, query bar, tabs) and small shared UI (metric grid, charts, tables, chips, sync notice).
- `features/{signal,llm,backtest,forecast}/` – one view per feature with its state hook.
- `lib/api/` – typed API client: `client.ts` (`fetchJson`, base URL), `auth.ts` (API key storage, `withAuthHeaders`), `endpoints.ts`, `types.ts`, `errors.ts`, `sse.ts`.
- `lib/dashboard-context.tsx` – symbol / benchmark / as-of date and results shared across routes.
- `lib/sync-retry.ts` – automatic retry (max 10 tries) for HTTP 503 `sync_pending`.
