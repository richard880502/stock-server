# 策略研究：如何提出並驗證策略

目標：AI 提出的每個策略建議，都由確定性回測背書；AI 只負責選參數與解讀，不負責計算。

## 可表達的策略空間

回測引擎目前是「單一標的、多頭/空手、次一交易日開盤成交」，由 `BacktestSpec` 參數化：

| 參數 | 意義 | 限制 |
|---|---|---|
| `symbol` / `benchmark` / `market` | 標的、比較基準、市場環境 | 先用 `search_symbol` 確認代號 |
| `strategy_version` | 訊號版本（預設 `regime_v1`） | 不要自創版本字串 |
| `start` / `end` | 回測區間 | `start < end` |
| `entry_score` | 訊號分數 ≥ 此值才進場 | 0–100 |
| `exit_score` | 分數 ≤ 此值出場 | 必須低於 `entry_score` |
| `fee_bps` / `slippage_bps` | 手續費、滑價（基點） | 0–1000，不得為了美化績效設為 0 |
| `use_chip_filter`, `chip_entry_score`, `chip_exit_score` | 加上籌碼分數門檻 | 出場門檻須低於進場門檻 |
| `sell_tax_bps`, `initial_capital` | 賣出稅、初始資金 | MCP `start_backtest` 未暴露，使用預設值 |

無法表達的構想（多標的輪動、停損停利、做空、槓桿）：直接告知使用者目前引擎不支援，
不要用口頭估算代替回測。

## 流程

1. **釐清假設**：用一句話寫出（例如「市場 risk_on 且外資連買時，趨勢股勝率較高」）。
2. **看現況**：`get_market_environment`、`analyze_symbol`、`analyze_chip_flow` 確認假設在現在是否成立。
3. **選參數**：把構想轉成上表參數。先用預設值跑基準版本，再最多做少量變體
   （例如 `entry_score` 65/70/75，或開關 `use_chip_filter`），避免參數窮舉造成過度配適。
4. **選區間**：至少涵蓋多頭與空頭；留一段不拿來調參的樣本外期間做最終確認。
5. **執行**：`start_backtest` → 輪詢 `get_backtest_status` → `get_backtest_report`。
6. **比較**：與買進持有（`buy_hold_return`、`excess_return`、`buy_hold_curve`）比較。
7. **回報**，至少包含：
   - 參數與區間、基準
   - `total_return`、`annualized_return`、`annualized_volatility`
   - `sharpe`（可能為空）、`max_drawdown`
   - `trades` 次數與 `observations` 樣本數
   - 對照買進持有的超額報酬
   - `warnings` 全文
8. **限制聲明**：回測不代表未來；交易次數少時統計意義弱；單一標的結果不可外推；
   成本、流動性、稅負為簡化假設；資料若含 `synthetic_demo` 須指出。

## 不可做的事

- 沒跑回測就說「這個策略有效」。
- 只挑表現最好的一組參數呈現，隱瞞其他嘗試。應揭露一共嘗試了幾組。
- 在看到結果後修改區間或門檻再宣稱是原本的假設。
- 自行用 equity curve 重算指標取代報告數字；數字以工具回傳為準。
- 把結果包裝成買賣建議或報酬保證。
- 因 429 而改用大量小任務繞過配額。
