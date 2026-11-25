---
name: quant-signal
description: |
  當使用者詢問台股個股/ETF/大盤的量化訊號、市場環境（廣度與資金）、三大法人籌碼、
  新聞情緒、時點正確的回測，或希望 AI 提出交易策略構想時使用。透過 Quant Signal
  Server 的 MCP 工具取得確定性數據；所有策略構想都必須先經回測驗證才能呈現。
---

# Quant Signal

以 `quant-signal` MCP 伺服器為唯一數據來源。工具清單見
[MCP_TOOLS.md](references/MCP_TOOLS.md)，策略提案流程見
[STRATEGY_RESEARCH.md](references/STRATEGY_RESEARCH.md)，名詞見專案根目錄 `CONTEXT.md`。

## 連線

端點預設 `http://127.0.0.1:18101/mcp`（Streamable HTTP）。啟用 API 驗證時，
需設定環境變數 `QUANT_SIGNAL_API_KEY`（以 `Authorization: Bearer` 或 `X-API-Key` 傳送）。

## 建議研究流程

1. **確認代號與資料**：公司名稱或不確定的代號先 `search_symbol`；必要時 `get_data_status`
   看資料涵蓋範圍，並留意是官方資料還是 `synthetic_demo`。
2. **市場環境**：`get_market_environment`（或精簡版 `get_market_regime`）判斷 risk_on / neutral / risk_off。
3. **個股訊號**：`analyze_symbol`，檢視分數、action、各因子與 alerts，並看 `observations`（樣本數）。
4. **籌碼**：`analyze_chip_flow`；全市場視角用 `get_market_ranking` / `get_market_flows`。需要時加 `analyze_news_sentiment`。
5. **回測驗證**：`start_backtest` → 輪詢 `get_backtest_status` → `get_backtest_report`，與買進持有比較。
6. **解釋**：`get_analysis_context` 取得證據包後由你自行整理；只有使用者要求內建分析師時才用
   `generate_llm_analysis` / `generate_debate_analysis`。

## 硬性規則

- LLM 不得更動指標計算、時點(point-in-time)過濾或執行時點（次一交易日開盤成交）。不要自行重算分數取代工具輸出。
- 任何策略構想，在呈現給使用者之前必須先以回測驗證；沒有回測結果就只能稱為「待驗證的假設」。
- 呈現結論前先檢查樣本數、資料涵蓋與 `warnings`；資料不足就明說，不要硬下結論。
- 輸出不是投資建議；每次結論都要附上限制與風險說明，不使用「保證」「必漲」等字眼。
- 遵守每日配額（LLM、同步、回測等）。收到 HTTP 429 就停止重試，告知使用者額度已滿；
  不要用迴圈暴力重試或拆成大量小回測繞過限制。
- 工具回傳 `sync_pending`（資料同步中）時，等待後重試一次即可，不要連續重打。
- 日期一律用 `YYYY-MM-DD`；`as_of` 省略代表今天。
- 不要把 API 金鑰寫進回覆或檔案。
