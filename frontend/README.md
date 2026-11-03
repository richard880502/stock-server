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

- `app/` – routes only: `/` (今日訊號), `/market` (市場), `/backtest`, `/forecast`, plus the root layout, `globals.css` (Tailwind entry) and `styles/` (the design system, below).
- `components/` – app shell (login gate, sticky header, query bar with the symbol combobox, view tabs, theme toggle) and shared UI (card, stat, chips, direction values, empty states, notices, tables) plus `charts/` (SVG charts).
- `features/{signal,market,llm,backtest,forecast}/` – one view per feature with its state hook.
- `lib/api/` – typed API client: `client.ts` (`fetchJson`, base URL), `auth.ts` (API key storage, `withAuthHeaders`), `endpoints.ts`, `types.ts`, `errors.ts`, `sse.ts`.
- `lib/dashboard-context.tsx` – symbol / benchmark / as-of date and results shared across routes.
- `lib/sync-retry.ts` – automatic retry (max 10 tries) for HTTP 503 `sync_pending`.

## Design system

Direction: a calm research notebook rather than a trading terminal. Warm neutral
surfaces, one blue accent for interaction, generous line height for Traditional
Chinese, and no glow or gradient decoration. Each view leads with the verdict
(`.hero` on the signal page, the result headline on backtest / forecast), then
the evidence, with detail behind `<details>` disclosures.

Stylesheets live in `app/styles/` and are imported by `app/layout.tsx` in this
order: `tokens.css` (variables) → `base.css` (element defaults, focus, motion
preferences) → `layout.css` (shell, header, grid) → `components.css` →
`charts.css` → `views.css` → `market.css`. Tailwind 4 is loaded from `globals.css` for its
preflight and utilities; the system itself is plain CSS custom properties.

- **Tokens** – colour, type scale (`--text-*`), 4px spacing scale (`--space-*`),
  radius, elevation (`--shadow-*`), motion (`--dur-*`, `--ease-out`). Colours are
  `light-dark()` pairs, so one declaration serves both themes.
- **Themes** – follow `prefers-color-scheme`; the header toggle cycles
  跟隨系統 → 淺色 → 深色 and stores the choice in `localStorage`
  (`quant_signal_theme`, access wrapped in try/catch). A small inline script in
  `<head>` applies a pinned theme before first paint (`lib/theme.ts`).
- **Taiwan colour convention** – red = up / gain, green = down / loss, via
  `--up` / `--down` (`.tone-up`, `.tone-down`, `<Direction>`). They are the only
  red and green in the UI: accent, chart series and warnings use blue, grey and
  amber. Direction always also carries a sign (`+` / `−`), an arrow or a label.
  Alert severity uses amber with an icon and a 高 / 中 / 資訊 label.
- **Accessibility** – text and control tokens are checked for WCAG AA in both
  themes (text ≥ 4.5:1, control borders ≥ 3:1), visible `:focus-visible`
  outlines, 44px touch targets, skip link, and `prefers-reduced-motion` removes
  animation.
- **Symbol search** – `components/symbol-combobox.tsx` implements the ARIA
  combobox pattern against `GET /api/v1/instruments/search`; `/` focuses it,
  arrows / Enter / Esc work, recent symbols (`quant_signal_recent_symbols`) show
  when the field is untouched, and a well-formed code unknown to the search can
  still be submitted (the backend syncs it on demand).
- **Charts** – no chart library. `components/charts/chart-frame.tsx` is the
  shared shell (responsive width, hover / touch crosshair snapping to the nearest
  point, tooltip, arrow-key navigation with a live readout, legend);
  `equity-chart.tsx` and `scenario-chart.tsx` supply geometry and series. Series
  colours are `--series-*` tokens; each chart has a text / table alternative.
- **States** – skeletons (`.skeleton`) while loading, `EmptyState` for nothing
  yet, `ErrorBanner` with retry for failures, and the amber `SyncPendingNotice`
  (with a countdown and progress bar) for HTTP 503 `sync_pending`.

## Market view (`/market`)

- **Data** – `GET /api/v1/market/{rankings,flows,data-status}` (`lib/api/endpoints.ts`).
  `features/market/use-market-resource.ts` loads one endpoint per query key, keeps
  the previous data dimmed while a new query loads, and re-polls every 15 s while
  the response says `backfill_pending`. The as-of date comes from the shared query
  bar (debounced by `use-committed-as-of.ts`).
- **Filters in the URL** – `?market=TW&window=5&metric=foreign_net&range=60&cum=1`
  (`use-market-params.ts`); defaults are omitted and bad values fall back.
- **Boards** – `ranking-board.tsx` renders a real table; inside a container query
  narrower than 33rem each row becomes a card. Clicking a row calls
  `refresh({ symbol })` on the dashboard context and navigates to `/`.
- **Honest data** – a session whose report is not out yet comes back from the API
  as zeros with `institutional_reported` / `credit_reported` false; charts hatch
  those days and break lines instead of drawing 0. NT$ flow amounts are labelled
  估算 (net shares × close). No broker-branch ("主力") data exists, so nothing is
  labelled that way.
- **Charts** – `charts/flow-chart.tsx` (grouped daily bars + optional cumulative
  panel) and `charts/trend-chart.tsx` (single series and advancing / declining
  counts) reuse `ChartFrame`. Institution series use `--series-foreign / -trust /
  -dealer` (blue, amber, violet); red / green appear only as up / down.
