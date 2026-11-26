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
- Redis contains latest quotes with a one-day TTL and ephemeral demand/health.
  Losing Redis causes renewed connections and subscriptions. It is not the
  historical store.
- The collector batches PostgreSQL minute writes every five seconds. On a
  database outage it keeps a bounded 10,000-minute backlog, reports storage
  failures in its logs, and discards the oldest pending minute if full.
  Captured minutes are explicitly partial because subscription can begin
  mid-minute and reconnects can miss trades. They are never used as complete
  official daily bars. Only demanded stocks are recorded.

## Read APIs

Both endpoints use the application's existing authentication:

- GET /api/v1/quotes/stream?symbols=2330.TW,6488.TWO yields event: state with
  status and quotes. Prices are TWD, change_pct is a ratio (0.025 = 2.5%),
  total_volume is in lots, and timestamps include time zones.
- GET /api/v1/quotes/minutes?symbol=2330.TW&limit=120 returns captured minutes
  oldest first. The optional before parameter is a time-zone-aware ISO timestamp.
  observed_volume_change_lots is the change between the first and last observed
  cumulative volume, rather than a guaranteed full-minute volume.

Keep API_AUTH_ENABLED=true for a multi-user deployment. Verify that your market
data entitlement covers displaying data to the intended audience before enabling
QUOTES_ENABLED for those users. Real-account login and subscriptions still need
an acceptance check in the deployment environment.
