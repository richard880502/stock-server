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

### Official events, risk flags and monthly revenue

The sync worker (inside the ingest service) also collects structured, point-in-time
market data from the TWSE / TPEx OpenAPI (free, JSON, no key). Disable it with
`OFFICIAL_EVENTS_ENABLED=false`.

| Data | TWSE (`https://openapi.twse.com.tw/v1`) | TPEx (`https://www.tpex.org.tw/openapi/v1`) |
| --- | --- | --- |
| 重大訊息 material announcements | `/opendata/t187ap04_L` | `/mopsfin_t187ap04_O` |
| 處置股 disposition | `/announcement/punish` | `/tpex_disposal_information` |
| 注意股 attention | `/announcement/notice`, `/announcement/notetrans` | `/tpex_trading_warning_information`, `/tpex_trading_warning_note` |
| 除權息預告 ex-rights schedule | `/exchangeReport/TWT48U_ALL` | `/tpex_exright_prepost` |
| 月營收 monthly revenue | `/opendata/t187ap05_L` | `/mopsfin_t187ap05_O` |

**These endpoints only return the latest period** (the most recent announcements, the
currently active flags, the latest reported month). There is no historical backfill:
history accumulates from the day the worker starts fetching, and an `as_of` before that
day returns nothing. Only four-digit common-stock codes are stored; warrants, ETFs and
depositary receipts are skipped.

Schedule (Asia/Taipei, guarded by `fetch_coverage` so a restart never refetches; a failed
fetch is logged and retried after 10 minutes without touching the price sync):

- risk flags and ex-rights: weekdays after 17:00 and again from 08:00 the next morning
- material announcements: weekdays 07:00-22:00, every 30 minutes
- monthly revenue: daily on days 1-15 of the month (companies file through the 10th),
  weekly otherwise

Tables (migration `0015_market_events`): `market_events` (material + ex-rights),
`market_risk_flags`, `monthly_revenue`. Every row has `available_at`, the time **we**
fetched it (never the publication date), and a `revision`. Re-fetching identical content
is a no-op; changed content (a restated month, an extended disposition, a changed
dividend) is stored as `revision + 1` with a new `available_at`, so as-of reads
(`available_at <= as_of`, highest visible revision) answer "what did we know on date D".
Monthly revenue is in NT$ thousands (仟元), as published.

Read API (`as_of` defaults to today, `days` is 1-365):

```bash
curl "http://127.0.0.1:18100/api/v1/stocks/2330.TW/events?as_of=2026-11-26&days=30"
curl "http://127.0.0.1:18100/api/v1/market/risk-flags?as_of=2026-11-26"
```

`/stocks/{symbol}/events` returns the stock's announcements and ex-rights schedule with a
rule-based `category` (buyback, capital_increase, m_and_a, major_contract, guidance,
clarification, litigation, dividend, executive_change, other) and coarse `polarity`
(`quant/event_classifier.py`, keyword rules, no LLM), its active disposition/attention
flags, and revenue metrics (`quant/revenue.py`: YoY, MoM, cumulative YoY, acceleration =
this month's YoY minus the average of the previous three, consecutive positive-YoY
months, 12-month-high flag). Attention flags are shown for the announcement day and the
next weekday (no holiday calendar). The MCP tools `get_stock_events` and
`get_market_risk_flags` return the same payloads.

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
    "slippage_bps": 5,
    "include_live_today": false,
    "use_chip_filter": true,
    "chip_entry_score": 55,
    "chip_exit_score": 40
  }'
```

成本預設採台股規則（`domain/tw_trading.py`）：手續費 0.1425%（可用 `commission_discount` 打折）每筆最低 NT$20、賣出證交稅股票 0.3% / ETF 0.1%（代號 `00` 開頭視為 ETF），另加 `slippage_bps`。`fee_bps` / `sell_tax_bps` 留空即用此規則，填任一欄則回到舊的固定 bps 模型。`include_live_today=true` 且 `quotes_enabled` 時，若今日尚無官方日線，會用 `quote_minutes` 補一根暫定 K 棒（結果 `provisional_bar` / `as_of_time` 標示）。

Use the returned ID:

```bash
curl "http://127.0.0.1:18100/api/v1/backtests/JOB_ID"
```

## LLM analysis

The server supports two LLM paths:

1. An external agent calls MCP tools and performs its own reasoning.
2. The built-in analyst sends a validated evidence bundle to a model: Claude
   through the Anthropic SDK, or any OpenAI-compatible server.

The built-in analyst receives:

- the symbol's deterministic signal
- the market breadth and capital environment
- stock-level institutional flows and optional licensed branch analysis
- anomaly evidence
- an optional completed backtest report

It cannot modify indicator formulas, execution timing, transaction costs, or
historical data.

### Claude (Anthropic API)

```env
LLM_ENABLED=true
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<set in your secret store, never in git>
ANTHROPIC_MODEL=claude-opus-5-5   # default
ANTHROPIC_EFFORT=medium           # low | medium | high | xhigh | max
ANTHROPIC_MAX_TOKENS=16000        # includes thinking
```

Each call streams, uses adaptive thinking at `ANTHROPIC_EFFORT`, and opts into
server-side refusal fallbacks (`server-side-fallback-2026-07-01`,
`fallbacks: "default"`), so a request a safety classifier declines is retried on a
fallback model inside the same call. A request that is still refused, or an
answer cut off at `ANTHROPIC_MAX_TOKENS`, returns HTTP 502 with the reason.
`LLM_MODEL`, `LLM_TEMPERATURE` and `LLM_MAX_TOKENS` apply only to the
OpenAI-compatible provider. The debate judge can use Claude independently with
`JUDGE_LLM_ENABLED=true`, `JUDGE_LLM_PROVIDER=anthropic` and an optional
`JUDGE_ANTHROPIC_MODEL` (defaults to `ANTHROPIC_MODEL`).

Every analysis is billed by Anthropic per token; the daily per-user `llm` quota
still applies. `GET /api/v1/llm/status` reports `provider` and the model in use.

### OpenAI-compatible server

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

### News pipeline (消息面)

**Who fetches.** Only the ingest service. API, MCP and the compute worker never
fetch news and no request ever triggers a fetch. The ingest service runs a `news`
task of its own (not inside the Shioaji collector, so a news failure cannot stall
prices) at each Taipei time in `NEWS_SYNC_TIME` (default `08:30,14:10`, every day;
`NEWS_SYNC_ENABLED=false` disables it). Scope is every user's watchlist plus the
tracked instruments, watchlist first, capped by `NEWS_SYNC_MAX_SYMBOLS` (60).
Symbols are fetched one after another with a pause (`NEWS_SYNC_DELAY_SECONDS`,
at least 1 s) between Google queries, and each (symbol, slot) is recorded once in
`fetch_coverage`, so restarts do not refetch. News is only stored for symbols that
already have an `instruments` row (adding to a watchlist creates it through the
background sync); other symbols are skipped and counted in the log. The manual
trigger still works: `uv run quant-signal-sync-data news --symbol 2330.TW`.

**Sources.**

- Google News RSS, queried per company name; `pubDate` is the real publish time.
- Optional SearxNG (`SEARXNG_PROXY_URL`/`SEARXNG_PROXY_KEY`); results have no
  trustworthy publish time and are stamped with the fetch time.
- Any RSS/Atom feed: put URLs, comma separated, in `NEWS_FEED_URLS`. Each feed is
  read whole once per slot and each item is filed under the symbols whose stock
  code (digit-bounded, not beside 年/月/元/點 and similar) or company name (at
  least two Chinese characters; two-character names must not sit inside a longer
  word) appears in the headline. To add a feed, append its URL; nothing else changes.
- A self-hosted [RSSHub](https://docs.rsshub.app/) is just another feed URL, for
  example `NEWS_FEED_URLS=http://rsshub:1200/<route>`. RSSHub is AGPL-3.0: run it
  **unmodified, on the private network only**, never exposed to users, and do not
  vendor its code here. It is not a core source (it has no cnyes, PTT or MOPS
  routes), only an optional extra.

**Point in time.** Each row has `published_at` (publisher clock, used for display
and recency) and `available_at` (when this system first held it). An as-of read
only sees rows with both `published_at` and `available_at` before the end of the
as-of day in Asia/Taipei, so a headline published on 11/20 but fetched on 11/26 is
invisible to an 11/21 analysis. Repeat fetches never rewrite `available_at`.

**De-duplication.** By `(url, source)` and by a normalized headline hash per symbol
(punctuation, whitespace and a trailing " - media name" removed), so syndicated
copies count once.

**Classification.** One batched LLM call per symbol per run (at most 20 headlines).
Each headline gets a random short `id` for that batch (never its position), and the
model returns `{"results": [{"id", "label", "score"}]}`. An entry that is missing,
unknown, duplicated or sign-inconsistent stays **unclassified** (no score, no label)
instead of becoming a fake neutral, and is retried by a later run while the item is
under 7 days old; unclassified rows never count in the aggregate. Each label records
`classified_at`; an as-of read before it sees the item as unclassified. Already
classified items are never classified again. Classification uses
`NEWS_ANTHROPIC_MODEL` (default `claude-haiku-5-5`) whenever `LLM_PROVIDER=anthropic`,
so the analyst/debate models (e.g. Opus) are not spent on headlines; set it to another
model, or empty to reuse `ANTHROPIC_MODEL`.

**Deployment.** The classification runs in the *ingest* service, so that service needs
`LLM_ENABLED=true`, `LLM_PROVIDER` and (for Claude) `ANTHROPIC_API_KEY` in its
environment (the compose files pass them through). Without them items are stored
unclassified and are only classified later, within the 7-day window, once the LLM is
configured.

**Hardening.** Feed and search responses are capped at 2 MB and 200 items; XML with a
DOCTYPE/ENTITY declaration is refused; only `http(s)` URLs are stored; zero-width and
bidi control characters are removed from headlines; requests send a normal
User-Agent. Headline-hash dedupe only looks at the last 7 days (a recurring headline
is not dropped forever); stories published more than 30 days ago are not ingested.
HTTP clients live for one whole run (not per symbol), every database phase uses its own
short session, and a symbol whose sources all failed counts as failed (retried up to
three times per slot) and is not recorded as done.

**In the debate.** The aggregate `news_sentiment` (score, label, count; no raw
headlines; only items from the 14 days before `as_of`, so stale headlines do not
dominate) stays in the evidence. The newest 8 distinct items from the last 7
days known at `as_of` are added as separate evidence ids `news:<8 hex>`; the model
sees source, time, label and a truncated headline (never the URL) and may cite a
single item in `evidence_refs`. The response's `evidence[].details` carries the
full headline and URL so a client can resolve an id. Prompts state that headlines
are untrusted data (instructions inside are ignored), weigh less than quantitative
evidence and official events, must be called out when they conflict with it, and
that missing news is a limitation, never something to invent. The risk and judge
calls get the items once, with 60-character headlines.

```bash
uv run quant-signal-sync-data news --symbol 2330.TW
curl "http://127.0.0.1:18100/api/v1/news-sentiment/2330.TW?as_of=2026-07-31"
```

`analyze_news_sentiment` (MCP) and `GET /api/v1/news-sentiment/{symbol}` aggregate
the point-in-time-filtered headlines into one `NewsSentimentSnapshot`, included as
`news_sentiment` in the evidence bundle for the single-shot and debate analysts.

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
request from then on. Both values are required: with a password but no key,
`/auth/config` reports `password: false` and the login route answers 503
instead of handing out an empty key. This only actually restricts data once
`API_AUTH_ENABLED=true` — a password screen in front of an open API protects
nothing, since the API would still answer direct requests either way.

## Syncing data

The database is the source of truth. Analysis, forecasts, backtests and the LLM
analyst read only PostgreSQL; **no request path calls TWSE or TPEx.**

```text
request ──► missing history? ──► enqueue one deduplicated sync job ──► 503 "資料同步中" + Retry-After
                                         │
daily 15:30 Taipei (weekdays) ───────────┤
                                         ▼
                          sync worker (single, throttled) ──► fetch only missing months ──► PostgreSQL
```

- **Coverage ledger (`fetch_coverage`).** Each fetched month (prices) or day
  (institutional flows) is recorded as `ok` or `empty`. A slice counts as
  covered only if it was fetched *after* it fully closed, so closed months are
  requested once and the open month is refreshed. Empty slices (holidays,
  months before a listing) are remembered too. A past month cut short by the
  requested `end` is never marked complete.
- **Incremental adjustments.** Forward-adjustment factors resume from the last
  stored exchange bar, so a refresh asks for corporate actions since that point
  instead of re-downloading everything since 2003. Synthetic demo bars never
  seed the factor.
- **Durable queue (`sync_jobs`).** At most one queued/running job per
  `(kind, symbol)` — ten simultaneous requests for one symbol cause one crawl.
  Failed jobs retry up to `SYNC_MAX_ATTEMPTS` with backoff; jobs stuck in
  `running` are requeued after `SYNC_STALE_AFTER_SECONDS`.
- **Daily refresh.** After each time in `SYNC_DAILY_TIME` (Taipei, weekdays,
  default `15:30,21:45`) the worker queues one price job per tracked symbol, one
  institutional-flow job per stock, and one whole-market snapshot job per
  exchange. The evening run exists because margin reports come out after the
  institutional ones.
- **Shared reports.** The market-wide TWSE T86/MI_QFIIS and TPEx daily-trade
  files are cached once settled, so N symbols cost one download per day.

The sync loop runs inside `quant-signal-worker` next to the backtest loop; set
`SYNC_ENABLED=false` to disable it. Run **one** worker instance: the request
interval lives in the providers, so replicas would multiply the load.

When a symbol has fewer than 60 point-in-time bars, the analysis endpoints
return `503` with `{"detail": "...", "sync_pending": true, "symbol": "..."}` and
a `Retry-After` header; MCP tools return the same message as a tool error. If a
sync already ran this month and the symbol is still short (a new listing), the
normal "insufficient history" error is returned instead of waiting forever.

`POST /api/v1/sync/symbol` (and the `sync_symbol_data` MCP tool) still run a
synchronous backfill for one symbol, but now through the same incremental
logic, so repeating it is cheap:

```bash
curl -X POST "http://127.0.0.1:18100/api/v1/sync/symbol" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "3016.TW", "start": "2024-01-01"}'
```

Not yet incremental: `quant-signal-sync-data market` (market breadth) still
walks every trading day in its warm-up window, because the rolling breadth needs
each day's quotes. See *Market rankings and capital flow* for the stored
whole-market table it can be rebuilt from.

## Market rankings and capital flow

The sync worker also stores every common stock's official end-of-day quote,
three-institution net buy/sell, and margin/short balance in
`market_stock_daily` (about 1,100 TWSE + 850 TPEx rows per trading day, from
`MI_INDEX`/`T86`/`MI_MARGN` and the TPEx equivalents). The first run fills
`SYNC_MARKET_LOOKBACK_DAYS` (default 520 calendar days, about 2,600 polite
requests; measured at about 5 sessions per minute per exchange, so plan on an
hour or more) of history automatically; afterwards only new days
are fetched. A day fetched before its reports were published is refetched the
next day, and holidays are remembered.

```bash
# Biggest foreign net buyers over 5 sessions, listed stocks (values in lots / 張)
curl "http://127.0.0.1:18100/api/v1/market/rankings?metric=foreign_net&window=5&market=TW&limit=20"
# Biggest net sellers: order=asc. Other metrics: trust_net, dealer_net, total_net,
# volume, turnover, change_pct, margin_change, short_change
curl "http://127.0.0.1:18100/api/v1/market/rankings?metric=change_pct&order=asc&window=20"
# Daily capital-flow series (estimated NT$) for charts
curl "http://127.0.0.1:18100/api/v1/market/flows?market=ALL&days=60"
curl "http://127.0.0.1:18100/api/v1/market/data-status"
```

- Rankings are point-in-time: `as_of` only uses reports published by then.
- Institutional and credit reports are published after the quotes. For those
  metrics the ranking uses the newest session whose report exists and returns
  `awaiting_report_date` for any newer session still waiting.
- `*_net_value` in the flow series is **net shares x that day's close**, an
  estimate and not the official NT$ amount. Sessions without a published
  report are flagged (`institutional_reported`, `credit_reported`), never zeroed.
- Names that did not trade on the latest session are not ranked.
- Only common stocks (4-digit codes not starting with 0) are covered, so ETFs
  are excluded. Broker-branch ("主力") data is a paid product and is not used.
- MCP exposes the same data as `get_market_ranking` and `get_market_flows`.
- **The market-environment score is derived from this table.** After each
  whole-market snapshot the same job builds any missing daily observation:
  advancers, MA20/MA60 participation, 52-week highs/lows and up/down volume come
  from the stored closes; only the official market-level figures (index close,
  total turnover, institutional NT$ flows, margin and short balances) are
  requested, once per new session. A session is built only when 252 earlier
  sessions of closes are stored (hence the 520-day first backfill) and a stock
  counts once it has 60 closes, as in the full rebuild; existing
  official observations are never rewritten, and a day whose capital reports
  were not out yet is retried the next day. A freshly deployed worker therefore
  backfills and keeps `/market-environments/TW` current on its own;
  `quant-signal-sync-data market` remains only as a manual full rebuild.

## All listed stocks, quick view and watchlist

Search (`GET /api/v1/instruments/search?q=`) covers **every** listed stock in the
latest `market_stock_daily` session, by code or name, merged with the instruments
that already have stored history. Each result carries `tracked`: `true` means the
adjusted history is stored (signals, backtests and forecasts answer immediately),
`false` means the stock is only known from the whole-market snapshot.

```bash
# Instant, no exchange calls: ~17 months of quotes, institutional flows and credit
curl "http://127.0.0.1:18100/api/v1/stocks/2330/overview"
```

`/stocks/{symbol}/overview` (a bare code or `2330.TW`) is built purely from the
snapshot table, so any listed stock opens at once. `full_analysis` reports whether
the stored history is enough for the signal engine: `ready`; `preparing` (a
deduplicated background job for the sync worker was just queued, usually minutes);
or `insufficient` (already synced but too few bars, e.g. a new listing). The
five-session flow sums use the newest sessions whose institutional report is out;
`flows_through` says which date that is, since quotes land hours before the report.

Signed-in (Google) users get a personal watchlist, capped by `WATCHLIST_MAX_ITEMS`
(default 25; the add is serialized per user in the database, so concurrent requests
cannot exceed it):

```bash
curl http://127.0.0.1:18100/api/v1/watchlist                 # list + latest quote/flows
curl -X PUT    http://127.0.0.1:18100/api/v1/watchlist/2330   # add; 409 when full, 404 if unlisted
curl -X DELETE http://127.0.0.1:18100/api/v1/watchlist/2330   # remove
```

Adding a stock queues its history sync, so it is usually ready for full analysis by
the time it is opened. A watchlist needs a user session: API keys and the legacy
dashboard password have no user, and get 403. ETFs are not covered yet because
the snapshot only contains common stocks.

## Local development

The project requires Python 3.12 or newer. `uv` can install a managed Python:

```bash
uv sync
uv run --all-groups pytest
uv run --all-groups ruff check .
```

### Integration tests (real PostgreSQL / Redis)

`uv run --all-groups pytest` skips the database tests unless their URLs are set. CI
runs them in the job "Backend integration (Postgres + Redis)". Locally, use a
**dedicated, throwaway** database (the tests clear job queues and write rows, so never
point them at a database a live worker uses):

```bash
docker compose up -d postgres redis   # or any Postgres 17 / Redis 7
docker compose exec postgres psql -U quant -d postgres -c "create database quant_signal_ci"
export DATABASE_URL=postgresql+asyncpg://quant:quant@127.0.0.1:15432/quant_signal_ci
export QUANT_TEST_DATABASE_URL=$DATABASE_URL QUOTES_TEST_DATABASE_URL=$DATABASE_URL
export QUOTES_TEST_REDIS_URL=redis://127.0.0.1:6379/15   # use a spare Redis db number
uv run --all-groups alembic upgrade head
uv run --all-groups pytest tests/test_postgres_integration.py \
  tests/test_quote_storage_integration.py tests/test_schema_drift.py
```

Add `CI_REQUIRE_INTEGRATION=1` to turn skipped integration tests into failures (what
CI does). The tests clean up after themselves and can be run repeatedly. Without
`QUOTES_TEST_REDIS_URL` the Redis tests are skipped.

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
- `get_stock_events`
- `get_market_risk_flags`
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

Every tool's `as_of` is optional and defaults to today, matching the REST
API. If a symbol still doesn't have enough point-in-time history (the engine
requires 60+ bars), the error names the earliest synced date for that symbol
so an agent can retry with a valid `as_of`, or call `sync_symbol_data` first.

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
- `fetch_coverage`
- `sync_jobs`
- `market_stock_daily`
- `market_events`, `market_risk_flags`, `monthly_revenue`
- `users`, `user_sessions`, `usage_counters`
- `watchlist_items`

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
5. Notification adapters (the daily sync scheduler is done)
6. News evidence and multi-step agent policies

This software is a research scaffold, not investment advice or an automated
execution system.

## Forecast lab and backtest comparison

The dashboard now has three views: signals, strategy backtests, and forecasts.
Backtests accept initial capital, per-side fees/slippage, and an additional
sell-side tax in basis points. New reports include transaction costs, a
same-instrument buy-and-hold curve, and excess return. Both portfolios remain
marked to market at the end; neither assumes a forced closing sale. The selected
benchmark symbol still feeds the relative-strength signal, rather than the
buy-and-hold comparison.

`POST /api/v1/forecasts` accepts `symbol`, `as_of`, `horizon` (1, 5, or 20),
`lookback` (60–512), and `model` (`bootstrap` or `kronos`). Taiwan trading dates
come from the XTAI calendar; unforeseen closures are not guaranteed. All price
history is adjusted onto the latest raw-price scale. Baseline scenarios resample
historical daily log returns with a fixed seed. Their 10–90 percentile range is
an uncalibrated scenario interval, not a guaranteed confidence interval.

Baseline evaluation uses up to 24 non-overlapping chronological windows, filters
records by their availability at each origin, and reports normalized absolute
error, no-change baseline error, direction accuracy, and interval coverage.
**This evaluation describes the bootstrap model only, including when the displayed
forecast comes from Kronos.** Kronos forecasts do not drive strategy executions.

The optional Kronos-small sidecar is separate from the language-model analyst:

```sh
docker compose -f docker-compose.yml -f docker-compose.kronos.yml up -d --build
```

It uses a pinned upstream source commit, loads model weights into a persistent
cache, and returns 16 individual sampled paths instead of averaging samples
before calculating their range. Startup downloads weights from Hugging Face.
`GET /api/v1/forecasts/status` reports availability; failures never silently
substitute another model. On macOS the same server can run directly with PyTorch
MPS, `PYTHONPATH` pointing to the pinned Kronos checkout, and the API's
`KRONOS_URL=http://127.0.0.1:18102`. `KRONOS_DEVICE=cpu` forces CPU inference.

### Running Kronos on another machine

The inference service in `kronos-service/` is a standalone image, so it does not
have to share a host with the API. CI (`.github/workflows/kronos-image.yml`)
publishes CPU images with the weights baked in, for `linux/amd64` and
`linux/arm64`, to GitHub Container Registry:

```text
ghcr.io/<owner>/kronos-service:small   # NeoQuasar/Kronos-small (also :latest)
ghcr.io/<owner>/kronos-service:base    # NeoQuasar/Kronos-base, the largest open model
```

```bash
docker run -d --name kronos -p 8002:8002 \
  -e KRONOS_API_KEY="$(openssl rand -hex 24)" \
  ghcr.io/<owner>/kronos-service:small
```

Then point the API at it with `KRONOS_URL=https://your-host:8002` and the same
`KRONOS_API_KEY`; the API sends it as `Authorization: Bearer`, and the service
rejects `/predict` without it (`/health` stays open for probes). With no key set
the service is open, which is only appropriate on a private network. Put TLS in
front of it (a reverse proxy or tunnel) before exposing it publicly.

Optional service settings: `KRONOS_MODEL`, `KRONOS_TOKENIZER`, `KRONOS_DEVICE`
(`cpu`, `cuda:0`, `mps`), `KRONOS_THREADS`, `KRONOS_LOAD_MODEL=0` (start without
weights for smoke tests). The published images are CPU-only; a GPU needs a CUDA
build of PyTorch. Kronos-base is about 4x the size of small, so expect it to be
several times slower on CPU. `/predict` returns `{"paths", "model"}` so every
forecast records which model produced it.

## Coolify migration

Use the GitHub App with branch `main`, Docker Compose build pack, and
`/docker-compose.coolify.yml`. Only the `gateway` service needs a public HTTPS
domain, targeting port 8080. The frontend uses same-origin API URLs. PostgreSQL,
API, worker, MCP, and Kronos stay on the application network; `/mcp` is forwarded
through the gateway with existing API-key authentication.

Transfer existing `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, dashboard
credentials, API-auth settings, and LLM settings as private Coolify environment
variables. Rewrite only the database host in `DATABASE_URL` to `postgres:5432`.
Do not commit secrets. Restore a consistent PostgreSQL custom-format dump into
the new database, compare table counts, and verify authenticated requests,
backtest execution, and prediction before switching clients. Coolify is the only
production environment; the earlier Zeabur deployment is retired. The separate deployment
file does not publish database or inference ports and does not run demo seeding.
After preloading a complete verified model cache, `KRONOS_OFFLINE=1` skips Hub
network lookups on startup; leave it unset for first-time weight downloads.

### Independently managed Coolify services

The `deploy/coolify/split/` files each define one application: API, worker, MCP,
frontend, Kronos, and gateway. Configure each as its own GitHub-backed Coolify
application with the corresponding Compose path and repository root as the base
directory. The private `stock-private` network connects their unique `stock-*`
aliases. PostgreSQL is a native Coolify PostgreSQL resource on that destination;
its UUID is the database hostname. Only the gateway has a public domain.

Use watch paths so frontend changes deploy the frontend, model-service changes
deploy Kronos, and backend changes deploy API/worker/MCP. PostgreSQL never follows
Git pushes. The gateway uses runtime Docker DNS and retains the existing website
and MCP URLs. Kronos keeps the existing external model-cache volume. Database
migration uses a fresh native database volume and a verified dump; the former
stack's PostgreSQL volume is retained for recovery.
