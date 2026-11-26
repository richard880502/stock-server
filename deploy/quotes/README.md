# Shared Shioaji market data

The quote collector runs independently from the API, MCP, and backtest workers.
Only the collector receives SHIOAJI_API_KEY and SHIOAJI_SECRET_KEY.
It logs in with subscribe_trade=False and subscribes to regular-lot stock trades.

## Local Compose

Set the two credentials in the deployment environment and QUOTES_ENABLED=true.
Run from the repository root:

```sh
docker compose --profile quotes up -d --build
```

The API runs Alembic migrations. Redis has no published port.
The frontend's Latest trades section opens one authenticated SSE connection for
up to 25 stocks. Each API process shares a single one-second Redis reader across
its viewers, so Redis reads scale with API replicas and the union of symbols.

## Independent Coolify applications

Deploy deploy/coolify/split/quotes.yml on the existing stock-private network.
Use the same DATABASE_URL as the API. Apply migration 0011 through the API first.
Set QUOTES_ENABLED=true on the API and use
QUOTES_REDIS_URL=redis://stock-quote-redis:6379/0.
The collector application receives the two Shioaji credentials. The API receives
only the Redis address. No public collector or Redis domain is needed.

For another hosting provider, build deploy/quotes/Dockerfile, set the collector
environment variables from .env.example, and connect both services to the same
private Redis and PostgreSQL endpoints. For local Python use uv sync --extra quotes.
The SDK is pinned to 1.3.3 with its compatible Pydantic version in uv.lock.

## Multi-user behavior

- One 30-second Redis lease elects the collector. It renews every five seconds.
  Publication is fenced by the current lease token and rejects older ticks.
  Prefer one collector replica. A standby retries after leadership expires.
- Stocks requested by multiple viewers share a single upstream subscription.
  Demand expires 60 seconds after the last viewer leaves. The global union is
  limited by QUOTES_MAX_SYMBOLS (default 200). Per-account streams are capped by
  QUOTES_MAX_CONNECTIONS_PER_USER (default 5), including across API replicas.
- SSE rechecks authentication on reconnect every five minutes. Reverse proxies
  must allow streaming with buffering disabled. The bundled nginx does.
- Quotes retain their trade time and receive time. The frontend marks data older
  than 30 seconds and disconnected streams. No trade since subscription means
  there may be no quote yet, including outside trading hours.
- Each SSE quote includes a server-aggregated one-minute candle. The watchlist
  uses one stream for all rows and opens one 1/5-minute chart at a time. Drawing
  anchors use time and price coordinates, so they follow chart panning.
- Minute-history requests share a five-second Redis cache and a distributed
  loading lock to avoid a database query for every viewer opening the same chart.
- Redis contains latest quotes with a one-day TTL and ephemeral demand/health.
  Losing Redis causes renewed connections and subscriptions. It is not the
  historical store.
- The collector batches PostgreSQL minute writes every five seconds. On a
  database outage it keeps a bounded 10,000-minute backlog, reports storage
  failures in its logs, and discards the oldest pending minute if full.
  Captured minutes are explicitly partial because subscription can begin
  mid-minute and reconnects can miss trades. They are never used as complete
  official daily bars. Only demanded stocks are recorded.

## TAIEX futures (台指期)

The symbol TXF.TAIFEX is the near-month TAIEX futures contract. The collector
subscribes to Shioaji's continuous contract TXFR1 (FOP tick v1). Its ticks carry
the real contract code (for example TXFL6) and are published under TXF.TAIFEX.
Futures events have "unit": "contract" (口); stock events have "unit": "lot" (張).
The live candle volume is in contracts for futures and shares for stocks
("volume_unit"). Futures minutes follow the TAIFEX sessions: day 08:45-13:45 and
night 15:00-05:00. The night session counts toward the next trading day.
The collector logs in to Shioaji again at 08:30 and 14:55 Taipei while futures
are subscribed. Contracts are downloaded at login, so TXFR1 follows the monthly roll.

## Official minute backfill and the Shioaji query budget

Only the collector (and the manual CLI below) talks to Shioaji. The API, MCP and
users read PostgreSQL and Redis only. Shioaji limits history queries (snapshots,
ticks, kbars, credit, short) to 50 calls per 10 seconds, shared across these calls.
During the session the limits are 10 ticks and 270 kbars calls. Going over them
suspends the account for a minute, and repeated abuse can ban the IP or ID. Every
kbars, ticks or snapshots call in this codebase therefore goes through one budget
stored in Redis (quant:quotes:budget:*). The budget is shared by collector
restarts and the CLI:

- 40 calls per rolling 10 seconds (10 spare for clock skew and other clients).
- During the session, up to QUOTES_BACKFILL_INTRADAY_MAX kbars calls (default 150,
  max 250) and 8 ticks calls per trading day. "Session" is read broadly: weekdays
  08:00-14:30 plus the futures night session 15:00-05:00. The night session counts
  toward the day it started on.
- The budget fails closed. Without Redis, or once the budget is used up, the query
  is skipped and logged. It is never sent.

The backfill writes Shioaji kbars into quote_minutes with is_partial=false. Volume
is per minute (lots or contracts): first_total_volume=0 and
last_total_volume=volume. Live flushes never modify official minutes.

- Intraday: the first time a symbol is subscribed in a session, the minutes before
  the subscription minute are filled once. Existing rows are never touched, and
  the subscription minute and later minutes are left to the live feed. Each symbol
  is deduplicated per session through a Redis key with a two-day TTL. The task is
  skipped quietly once the intraday budget is used up.
- After the close: once per weekday at or after QUOTES_BACKFILL_AFTER_CLOSE
  (Taipei, default 14:45), the collector replaces up to QUOTES_BACKFILL_MAX_SYMBOLS
  symbols (default 300) with official minutes. These are the watchlist symbols,
  symbols seen live that day, and current subscriptions. A Redis key claims the
  day. On failure the claim is released so the run retries. If the budget runs out,
  the run retries after 15 minutes and skips symbols that were already completed.
  On holidays kbars returns nothing, so the run has no effect. Keep this time
  between 14:30 and 15:00. Earlier or later times draw on the session allowance.
- All history queries run one at a time from a single background queue.
- Each replaced day is compared with the live minutes. If fewer than 80% of
  closes match, the run logs "Official minutes disagree with live minutes". That
  points to a kbars label or time zone problem.

Collector settings (defaults shown):

```text
QUOTES_BACKFILL_ENABLED=true
QUOTES_BACKFILL_AFTER_CLOSE=14:45
QUOTES_BACKFILL_INTRADAY_MAX=150
QUOTES_BACKFILL_MAX_SYMBOLS=300
```

Run a manual catch-up inside the collector container. It opens a second Shioaji
connection (each account may have 5) and draws on the same budget:

```sh
quant-signal-backfill-minutes --date 2026-11-26 --symbol 2330.TW --symbol TXF.TAIFEX
```

## Read APIs

Both endpoints use the application's existing authentication:

- GET /api/v1/quotes/stream?symbols=2330.TW,6488.TWO yields event: state with
  status and quotes. Prices are TWD, change_pct is a ratio (0.025 = 2.5%),
  total_volume is in lots, and timestamps include time zones.
- GET /api/v1/quotes/minutes?symbol=2330.TW&limit=120 returns captured minutes
  oldest first. The optional before parameter is a time-zone-aware ISO timestamp.
  observed_volume_change_lots is the change between the first and last observed
  cumulative volume, rather than a guaranteed full-minute volume. Official rows
  (is_partial=false) hold the full-minute volume. volume_unit is "lot", or
  "contract" for TXF.TAIFEX.

Keep API_AUTH_ENABLED=true for a multi-user deployment. Verify that your market
data entitlement covers displaying data to the intended audience before enabling
QUOTES_ENABLED for those users. Real-account login and subscriptions still need
an acceptance check in the deployment environment.

## Health checks

The collector exposes an internal HTTP server on port 8003:

- /livez checks the event loop heartbeat, including when a replica is on standby.
- /readyz and /health require the leader lease, a logged-in Shioaji connection
  and a successful Redis ping. They do not require new trades, so a market closure
  does not make a connected collector unhealthy.

The container health check uses /livez (not /health) with a 90-second startup
grace period, so a standby replica or a new container waiting for the leader lease
during a rolling deploy is not reported unhealthy. Use /readyz for monitoring the
active leader.
The port is not published to the Internet. Health responses contain only boolean
status values and never credentials or account details.
