# MCP 工具參考

來源：`src/quant_signal/adapters/mcp/server.py`。所有日期參數為 `YYYY-MM-DD` 字串；`as_of` 省略 = 今天。
代號會自動去空白並轉大寫（上櫃股票以 `.TWO` 結尾，例如 `6488.TWO`；大盤指數如 `^TWII`）。

另有唯讀資源 `quant://server/info`（伺服器名稱、版本、能力清單）。

## 資料與搜尋

| 工具 | 參數 | 回傳 |
|---|---|---|
| `get_data_status` | 無 | 各資料序列（官方/synthetic）的日期涵蓋、來源、筆數 |
| `search_symbol` | `query: str`（公司名或部分代號） | 符合的標的清單（Instrument） |
| `sync_symbol_data` | `symbol: str`, `start: str`, `end: str \| None`（預設今天） | 單一標的官方 TWSE/TPEx 日線回補報告；可重複執行（增量）。消耗同步配額 |

## 訊號與市場環境

| 工具 | 參數 | 回傳 |
|---|---|---|
| `analyze_symbol` | `symbol`, `as_of?`, `benchmark?`, `strategy_version="regime_v1"` | `SignalSnapshot`：`score`(0-100)、`action`(increase/hold/reduce/avoid)、`regime`、`confidence`(0-1)、`factors`(trend/momentum/volume/relative_strength/risk_quality)、`alerts`、`data_available_at`、`observations` |
| `get_signal_alerts` | `symbol`, `as_of?`, `benchmark?` | 該標的 `alerts` 與數值證據，附 `signal_score`、`regime` |
| `get_market_environment` | `market`（如 `TW`）, `as_of?`, `strategy_version="market_env_v1"` | 完整 `MarketEnvironmentSnapshot`：`score`、`regime`、`confidence`、六項廣度/資金因子（participation, advance_decline, breadth_momentum, volume_breadth, institutional_flow, leverage_quality）、`alerts`、最新觀測 `latest` |
| `get_market_regime` | `market_symbol`（`^TWII` 視為 `TW`）, `as_of?`, `strategy_version="market_env_v1"` | 上者的精簡版：market, as_of, regime, score, confidence, factors, alerts, data_available_at |

## 籌碼與排行

| 工具 | 參數 | 回傳 |
|---|---|---|
| `analyze_chip_flow` | `symbol`, `as_of?`, `strategy_version="chip_flow_v1"` | `ChipFlowSnapshot`：官方三大法人流向，及（若有授權資料）分點活動 |
| `get_market_ranking` | `metric`（foreign_net / trust_net / dealer_net / total_net / volume / turnover / change_pct / margin_change / short_change，預設 foreign_net）, `market`（ALL/TW/TWO）, `window: int=1`（上限 60）, `order`（desc/asc）, `limit: int=20`（上限 100）, `as_of?` | 全市場排行；股數單位為張（1,000 股）；`asc` 看最大賣超/跌幅；時點正確 |
| `get_market_flows` | `market`（ALL/TW/TWO）, `days: int=60`（5–250）, `as_of?` | 每日資金流序列：外資/投信/自營淨額（估計新台幣）、成交值、融資融券餘額、漲跌家數 |

## 新聞

| 工具 | 參數 | 回傳 |
|---|---|---|
| `analyze_news_sentiment` | `symbol`, `as_of?`, `strategy_version="news_sentiment_v1"` | `NewsSentimentSnapshot`：以時點正確分類的新聞標題彙整情緒分數 |

## 回測（非同步 job）

| 工具 | 參數 | 回傳 |
|---|---|---|
| `start_backtest` | `symbol`, `start`, `end`, `benchmark?`, `market="TW"`, `strategy_version="regime_v1"`, `entry_score=70`, `exit_score=45`, `fee_bps=5`, `slippage_bps=5`, `use_chip_filter=False`, `chip_entry_score=55`, `chip_exit_score=40` | job（含 `id`、`status`、`spec`）。次一開盤成交的多/空手（long/cash）回測。消耗回測配額 |
| `get_backtest_status` | `job_id: str` | job 狀態（queued/running/completed/failed）；完成時含 `summary`（報告，不含 equity_curve 與 spec） |
| `get_backtest_report` | `job_id: str` | 完成時回傳完整 `BacktestReport`；未完成回 `{job_id, status, error}` |

注意：`start_backtest` 未暴露 `sell_tax_bps` 與 `initial_capital`（使用預設值 0 與 1,000,000）。
驗證規則：`start < end`、`exit_score < entry_score`、`chip_exit_score < chip_entry_score`，否則報錯。

## 證據包與內建 LLM

| 工具 | 參數 | 回傳 |
|---|---|---|
| `get_analysis_context` | `symbol`, `as_of?`, `market="TW"`, `benchmark="^TWII"`, `strategy_version="regime_v1"`, `backtest_job_id?` | 一包量化證據，**不呼叫任何 LLM**；適合由你自己解讀 |
| `generate_llm_analysis` | 同上 | 內建 LLM 分析師報告（需伺服器已設定 LLM；否則報錯）。消耗 LLM 配額 |
| `generate_debate_analysis` | 同上 | 多空辯論後綜合裁決報告（需已設定 LLM）。消耗 LLM 配額 |

## 常見錯誤

- 歷史不足（少於 60 根時點正確 K 線）：回 `sync_pending`，稍後重試一次。
- 429：每日配額已用盡，停止並回報使用者。
- 401：缺少或無效的 API 金鑰。
