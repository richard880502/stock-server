# Quant Signal Server

A clean, agent-agnostic foundation for deterministic daily market signals,
anomaly alerts, point-in-time backtests, PostgreSQL persistence, REST, and MCP.

This repository is an MVP. It intentionally keeps LLMs outside the quantitative
core: an agent may explain results, but it cannot change indicator math,
point-in-time filtering, execution timing, or performance calculations.

## What works

- Deterministic daily-bar signal engine
- Independent point-in-time market breadth and capital environment engine
- Common factors for indexes, ETFs, and stocks
  - trend
  - momentum
  - volume confirmation
  - benchmark-relative strength
  - risk quality
- Initial anomaly detection
  - abnormal volume
  - opening gaps
  - price/volume divergence
- Market environment factors
  - MA20 / MA60 participation
  - advance / decline breadth
  - 52-week high / low momentum
  - up-volume / down-volume breadth
  - institutional cash flow and futures positioning
  - margin and short-balance leverage quality
- Market alerts for narrow leadership, breadth breakdown, breadth thrust,
  institutional outflow, and leverage overheating
- Point-in-time revision filtering using `available_at`
- Next-open, long/cash backtest with fees and slippage
- Durable PostgreSQL backtest queue
- Independent worker process
- FastAPI endpoints
- MCP tools over stdio or Streamable HTTP
- Optional built-in LLM analyst over quantitative evidence
- OpenAI-compatible hosted or local model support
- Responsive web dashboard for signals, alerts, LLM guidance, and backtests
- Official TWSE provider for listed-symbol and TAIEX historical daily bars
- Official TWSE breadth, turnover, institutional-flow, margin, and short data sync
- Official TPEx provider for `.TWO` symbols, the TPEx index, and OTC market breadth
- Official stock-level foreign, investment-trust, and dealer daily flows
- Foreign ownership ratio and change when supplied by the exchange
- Optional licensed broker-branch concentration, cost, short-term activity, and win rate
- Point-in-time chip-flow filter for historical backtests
- Point-in-time-safe forward-adjusted prices from official ex-right/dividend references
- Data-source coverage status over REST and MCP
- Synthetic demo data for end-to-end testing

## Architecture

```text
Web dashboard -> FastAPI -> Application Services -> Signal Engine ----> PostgreSQL
                           |                    |-> Market Environment -|
                           |                    |-> Backtest Worker -----|
                           |                    |-> Built-in LLM analyst
External agent ----------> MCP -----------------|
```

The dependency direction is deliberate:

```text
domain <- quant/application <- database, REST, MCP, workers
```

`quant_signal.quant` and `quant_signal.backtest` do not import LangChain, MCP,
FastAPI, SQLAlchemy, or any LLM package.

## Quick start with Docker

```bash
cp .env.example .env
docker compose up --build -d postgres api mcp worker frontend
docker compose --profile demo run --rm seed
```

Services:

- REST API: `http://127.0.0.1:18100`
- OpenAPI: `http://127.0.0.1:18100/docs`
- MCP Streamable HTTP: `http://127.0.0.1:18101/mcp`
- Web dashboard: `http://127.0.0.1:13000`
- PostgreSQL: `127.0.0.1:15432`

Check a demo signal:

```bash
curl "http://127.0.0.1:18100/api/v1/signals/2330.TW?as_of=2026-07-31&benchmark=%5ETWII"
```

Check the point-in-time Taiwan market environment:

```bash
curl "http://127.0.0.1:18100/api/v1/market-environments/TW?as_of=2026-07-31"
```

Check whether each series is official or synthetic:

```bash
curl "http://127.0.0.1:18100/api/v1/data/status"
```

## Official TWSE and TPEx data

The included providers read official after-market reports without an API key.
Use `.TW` for listed stocks and `.TWO` for OTC stocks:

```bash
uv run quant-signal-sync-data symbol \
  --symbol 2330.TW --start 2024-01-01 --end 2026-07-31
uv run quant-signal-sync-data symbol \
  --symbol '^TWII' --start 2024-01-01 --end 2026-07-31
uv run quant-signal-sync-data symbol \
  --symbol 6488.TWO --start 2024-01-01 --end 2026-07-31
uv run quant-signal-sync-data symbol \
  --symbol '^TWOII' --start 2024-01-01 --end 2026-07-31
```

Build market breadth and cash-capital observations:

```bash
uv run quant-signal-sync-data market \
  --start 2026-04-15 --end 2026-07-31 \
  --warmup-calendar-days 400
uv run quant-signal-sync-data market \
  --market TWO --start 2026-04-15 --end 2026-07-31 \
  --warmup-calendar-days 400
```

Backfill stock-level institutional flows:

```bash
uv run quant-signal-sync-data institutions \
  --symbol 2330.TW --start 2026-04-15 --end 2026-07-31
uv run quant-signal-sync-data institutions \
  --symbol 6488.TWO --start 2026-04-15 --end 2026-07-31
```

Analyze the resulting chip signal:

```bash
curl "http://127.0.0.1:18100/api/v1/chip-flows/2330.TW?as_of=2026-07-31"
```

### Licensed broker-branch data

Complete daily broker-branch reports are paid exchange information products.
The application therefore never scrapes or fabricates them. Import a licensed
export normalized to these columns:

```text
trading_date,symbol,branch_code,branch_name,buy_shares,sell_shares,buy_amount,sell_amount,day_trade_buy_shares,day_trade_sell_shares,available_at,revision
```

The last four columns are optional. Import the file with:

```bash
uv run quant-signal-import-branches \
  --file licensed-branches.csv \
  --source licensed_twse_branch_report
```

Without a licensed import, institutional analysis remains active and the API
returns `branch_data_status: not_licensed_or_not_imported`. After import it also
calculates top-five buy/sell concentration, estimated branch cost, intraday
activity ratio, top buyers/sellers, and a five-session directional win rate
when enough history exists.

The Docker equivalent runs a one-shot daily sync for the default symbol:

```bash
docker compose --profile real-data run --rm sync
```

The warmup is required for point-in-time MA60 and rolling 52-week breadth. The
command respects a small interval between official requests and is safe to
rerun because database writes are idempotent.

Price adjustment behavior:

- Stock prices are forward-adjusted using each exchange's official
  ex-right/ex-dividend previous close and reference price.
- The factor changes on the event date, so a historical signal does not contain
  an adjustment caused by a future corporate action.
- Signals use adjusted closes; next-open backtests use adjusted opens.
- `adjusted_observations` in `/api/v1/data/status` exposes adjustment coverage.

Important limitations:

- Cash institutional flow, margin, and short balances are official exchange data.
- TAIFEX futures open interest is not connected yet and is neutralized in the
  current TWSE market observation.
- Real observations take precedence when they overlap `synthetic_demo` dates.

Queue a backtest:

```bash
curl -X POST "http://127.0.0.1:18100/api/v1/backtests" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "0050.TW",
    "benchmark": "^TWII",
    "market": "TW",
    "strategy_version": "regime_v1",
    "start": "2025-01-01",
    "end": "2026-07-31",
    "entry_score": 70,
    "exit_score": 45,
    "fee_bps": 5,
    "slippage_bps": 5,
    "use_chip_filter": true,
    "chip_entry_score": 55,
    "chip_exit_score": 40
  }'
```

Use the returned ID:

```bash
curl "http://127.0.0.1:18100/api/v1/backtests/JOB_ID"
```

## LLM analysis

The server supports two LLM paths:

1. An external agent calls MCP tools and performs its own reasoning.
2. The built-in analyst sends a validated evidence bundle to an
   OpenAI-compatible model.

The built-in analyst receives:

- the symbol's deterministic signal
- the market breadth and capital environment
- stock-level institutional flows and optional licensed branch analysis
- anomaly evidence
- an optional completed backtest report

It cannot modify indicator formulas, execution timing, transaction costs, or
historical data.

Configure a local OpenAI-compatible server:

```env
LLM_ENABLED=true
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen3:8b
LLM_API_KEY=local
```

When the API runs in Docker and the model runs on macOS:

```env
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

Restart the API and MCP containers after changing LLM settings:

```bash
docker compose up --build -d api mcp
```

Check configuration without exposing the key:

```bash
curl "http://127.0.0.1:18100/api/v1/llm/status"
```

Generate an LLM analysis:

```bash
curl -X POST "http://127.0.0.1:18100/api/v1/llm-analyses" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "2330.TW",
    "as_of": "2026-07-31",
    "market": "TW",
    "benchmark": "^TWII",
    "strategy_version": "regime_v1"
  }'
```

To ground the analysis in a completed backtest, add:

```json
{
  "backtest_job_id": "COMPLETED_JOB_UUID"
}
```

The prompt forces Traditional Chinese JSON and requires explicit limitations
when the data source is synthetic or the backtest is missing.

For a token-by-token conditional operation guide, use the Server-Sent Events
endpoint. The existing JSON endpoint remains available for programmatic and MCP
workflows:

```bash
curl -N -X POST "http://127.0.0.1:18100/api/v1/llm-analyses/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "2330.TW",
    "as_of": "2026-07-31",
    "market": "TW",
    "benchmark": "^TWII"
  }'
```

### Bull/bear debate analysis

`/api/v1/debate-analyses` (and the `generate_debate_analysis` MCP tool) runs the
same evidence bundle through three calls instead of one: a bull-case
researcher, a bear-case researcher, and a final synthesis that must explicitly
weigh which side's evidence is stronger. All three calls are bound to the same
deterministic JSON evidence as the single-shot analyst above — none of them
ever make a live external call themselves; any news evidence they see was
already fetched, classified, and persisted point-in-time beforehand (see
below).

```bash
curl -X POST "http://127.0.0.1:18100/api/v1/debate-analyses" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "2330.TW",
    "as_of": "2026-07-31",
    "market": "TW",
    "benchmark": "^TWII"
  }'
```

The response includes `bull_case`, `bear_case`, and a final `judgement` in the
same shape as `/api/v1/llm-analyses`, plus `model` (the bull/bear researcher
model) and `judge_model` (the synthesis model — the same as `model` unless
configured otherwise).

By default the same model argues every side of the debate and then judges
it. Set `JUDGE_LLM_ENABLED=true` plus `JUDGE_LLM_BASE_URL`/`JUDGE_LLM_MODEL`/
`JUDGE_LLM_API_KEY` to use an independent model for only the final synthesis
call — reduces the risk of a single model rubber-stamping its own debate.
Falls back to the bull/bear model when left disabled.

### News sentiment analyst

Unlike a live news-search tool call, this stays point-in-time-safe: headlines
are fetched and classified once, ahead of time, into the `news_items` table
with a real `published_at`, and analysis only ever reads headlines that were
already published as of the requested `as_of` date.

```bash
uv run quant-signal-sync-data news --symbol 2330.TW
curl "http://127.0.0.1:18100/api/v1/news-sentiment/2330.TW?as_of=2026-07-31"
```

`quant-signal-sync-data news` resolves the company name (via the instrument
search below), pulls headlines from Google News RSS — `pubDate` is a genuine
historical timestamp, safe for backfill — and, if `SEARXNG_PROXY_URL`/
`SEARXNG_PROXY_KEY` are set, also from a self-hosted SearxNG instance behind
the small authenticated proxy in `searxng-proxy/`. SearxNG's general search
results carry no reliable historical publish date, so those are stamped with
the sync run time and are only meaningful for *current* sentiment, never for
backfilling history. Each new headline is classified into
`bullish`/`neutral`/`bearish` by the configured LLM, bound only to the
headline text itself. `analyze_news_sentiment` (MCP) and
`GET /api/v1/news-sentiment/{symbol}` aggregate the point-in-time-filtered
headlines into one `NewsSentimentSnapshot`, and it's automatically included as
`news_sentiment` in the evidence bundle for the single-shot and debate
analysts above whenever classified headlines exist for that symbol.

## API authentication

Both REST (`/api/v1/*`) and the MCP `streamable-http` endpoint are open by
default for local development. Before exposing either publicly (for example,
connecting the MCP server to ChatGPT), set `API_AUTH_ENABLED=true` and mint at
least one key:

```bash
uv run quant-signal-manage-keys create --name chatgpt
uv run quant-signal-manage-keys list
uv run quant-signal-manage-keys revoke --id KEY_UUID
```

`create` prints the plaintext key once; only its SHA-256 hash is stored. Send
it as `Authorization: Bearer <key>` (the header ChatGPT/OpenAI's `mcp` tool
uses) or `X-API-Key: <key>`. `/health` and the MCP `stdio` transport (local
process only) never require a key.

### Frontend login gate

The dashboard has its own password screen, separate from the API keys above.
Set `DASHBOARD_PASSWORD` and `DASHBOARD_API_KEY` (a real key created with
`quant-signal-manage-keys`) on the `api` service. `POST /api/v1/auth/login`
checks the password and, on success, hands back `DASHBOARD_API_KEY`; the
browser stores it and attaches it as `Authorization: Bearer <key>` on every
request from then on. This only actually restricts data once
`API_AUTH_ENABLED=true` — a password screen in front of an open API protects
nothing, since the API would still answer direct requests either way.

## Syncing data on demand

`POST /api/v1/sync/symbol` (and the `sync_symbol_data` MCP tool) backfills one
symbol's official daily bars synchronously, choosing TWSE or TPEx by suffix:

```bash
curl -X POST "http://127.0.0.1:18100/api/v1/sync/symbol" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "3016.TW", "start": "2024-01-01"}'
```

This is the same path the `quant-signal-sync-data symbol` CLI uses, just
reachable over HTTP/MCP instead of a shell exec, so a symbol with no history
yet (the analysis tools require at least 60 point-in-time bars) can be synced
without dashboard/server access. Large date ranges take longer than the
request may want to wait on — for a full multi-year backfill, prefer the CLI.

## Local development

The project requires Python 3.12 or newer. `uv` can install a managed Python:

```bash
uv sync
uv run --all-groups pytest
uv run --all-groups ruff check .
```

Start only PostgreSQL:

```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run quant-signal-seed-demo
```

Run each process:

```bash
uv run quant-signal-api
uv run quant-signal-worker
uv run quant-signal-mcp
```

Run the frontend locally:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

The dashboard combines the deterministic signal, factor evidence, anomaly
alerts, optional LLM guidance, and durable backtest workflow. Set
`NEXT_PUBLIC_QUANT_API_URL` at frontend build time when the REST API is not
available at `http://127.0.0.1:18100`.

## MCP tools

The MCP server exposes:

- `get_data_status`
- `search_symbol`
- `sync_symbol_data`
- `analyze_symbol`
- `get_market_regime`
- `get_market_environment`
- `get_signal_alerts`
- `analyze_chip_flow`
- `analyze_news_sentiment`
- `start_backtest`
- `get_backtest_status`
- `get_backtest_report`
- `get_analysis_context`
- `generate_llm_analysis`
- `generate_debate_analysis`

`search_symbol` resolves a company name (e.g. `台積電`) or a partial ticker
(e.g. `2330`) to exact symbols, ranked by match quality. An external agent
should call it first whenever it only has a name or an uncertain ticker, then
pass the resolved symbol to the other tools. Company names are only
populated for symbols synced through `quant-signal-sync-data symbol`
(the official TWSE/TPEx response carries the name); demo-seeded symbols have
no name.

`get_analysis_context` is intended for an external agent. It returns one
validated evidence bundle without invoking the built-in LLM.

`generate_llm_analysis` invokes the optional configured model.

`generate_debate_analysis` runs a bull/bear debate over the same evidence
bundle before returning a synthesized verdict — see
[Bull/bear debate analysis](#bullbear-debate-analysis).

`get_market_regime` remains as a compact compatibility tool. `^TWII` is
translated to the `TW` market. New integrations should use
`get_market_environment` to receive all six factors, source observations, and
market alerts.

## Market environment model

The market environment is not inferred from one index ticker. Each daily
`MarketObservation` contains breadth and capital evidence with its own
`available_at`, source, and revision:

- advancing, declining, and unchanged issues
- issues above MA20 and MA60
- 52-week highs and lows
- up-volume and down-volume
- total turnover
- foreign, investment-trust, and dealer net flow
- futures net open interest
- margin and short balance

The six normalized factors are combined as follows:

```text
participation       25%
advance / decline   20%
breadth momentum    15%
volume breadth      15%
institutional flow  15%
leverage quality    10%
```

The combined regime is `risk_on` at 65 or above, `neutral` from 45 to 64.99,
and `risk_off` below 45. Backtests that specify `"market": "TW"` block new
entries and move to cash during a point-in-time `risk_off` environment.

The demo seed creates synthetic observations only to prove the data path. The
TWSE and TPEx providers supply official listed/OTC breadth, cash institutional
flow, margin, and short-balance data. TAIFEX futures positioning remains to be
connected.

## Research workflow direction

The next research layer should keep natural-language strategy design separate
from execution:

```text
Natural-language idea -> validated StrategySpec -> deterministic backtest
                      -> risk and robustness report -> versioned iteration
```

`StrategySpec` should explicitly record the universe, factor definitions,
rebalance schedule, weighting, benchmark, cost assumptions, and market-regime
gate. Generated code must not be executed directly without validation.

It also exposes the read-only resource:

```text
quant://server/info
```

### Remote MCP

Connect an MCP client to:

```text
http://127.0.0.1:18101/mcp
```

Use MCP Inspector during development:

```bash
npx -y @modelcontextprotocol/inspector
```

### Local stdio MCP

For an agent that launches the server as a subprocess:

```json
{
  "mcpServers": {
    "quant-signal": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/quant-signal-server",
        "run",
        "quant-signal-mcp"
      ],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "DATABASE_URL": "postgresql+asyncpg://quant:quant@127.0.0.1:15432/quant_signal"
      }
    }
  }
}
```

## Point-in-time rules

Every market observation has:

- `trading_date`
- `available_at`
- `ingested_at`
- `source`
- `revision`
- `payload_hash`

The signal engine excludes:

- observations after `as_of`
- revisions whose `available_at` is after `as_of`

The MVP uses daily decision dates. A production intraday version should replace
the date cutoff with an exact timezone-aware `decision_at`.

## Backtest semantics

The backtester uses this timeline:

```text
T-1 close: calculate signal using information available through T-1
T open:    apply the new target position and transaction costs
T -> T+1:  earn the open-to-open return
```

This prevents a same-bar close-to-open look-ahead.

Current strategy mechanics:

- long or cash
- enter when score is at least `entry_score`
- exit when score is below `exit_score`
- fees and slippage charged on position turnover

## Database tables

- `instruments`
- `bars_daily`
- `signal_snapshots`
- `backtest_jobs`

The first migration is under `migrations/versions/0001_initial.py`.

## Deliberate MVP limitations

- Daily bars only.
- No authentication on REST or MCP; bind to localhost only.
- Built-in LLM support currently targets OpenAI-compatible chat-completion APIs.
- The backtest is long/cash and single-asset.
- No benchmark performance report yet; benchmark bars currently affect relative strength.
- No portfolio optimizer.
- The MCP SDK is pinned to stable v1 (`<2`) to avoid an unreviewed major migration.

Do not expose the current MCP HTTP endpoint publicly until authentication,
authorization scopes, rate limits, and audit logging are added.

## Next implementation slices

1. TAIFEX futures point-in-time ingestion
2. Historical benchmark and event-study reports
3. Strategy definitions and immutable version hashes
4. Authentication and tool scopes
5. Daily scheduler and notification adapters
6. News evidence and multi-step agent policies

This software is a research scaffold, not investment advice or an automated
execution system.
