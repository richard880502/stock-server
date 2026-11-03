"use client";

import { useDashboard } from "@/lib/dashboard-context";

export function BacktestForm() {
  const { query, dataStatus, backtest } = useDashboard();
  const { form, updateForm, loading, run } = backtest;
  const hasRealMarketData = dataStatus?.market_series.some(
    (series) => series.key === query.market && series.is_real,
  );

  return (
    <form className="backtest-form" onSubmit={run}>
      <div>
        <p className="eyebrow">POINT-IN-TIME BACKTEST</p>
        <h2>驗證訊號，不替歷史找答案。</h2>
        <p>以前一日收盤資訊產生訊號，下一交易日開盤成交，並計入費用與滑價。</p>
      </div>
      <label>
        <span>回測起始日</span>
        <input
          type="date"
          value={form.start}
          max={query.asOf}
          onChange={(event) => updateForm({ start: event.target.value })}
        />
      </label>
      <div className="threshold-row">
        <label>
          <span>進場分數</span>
          <input
            type="number"
            min="1"
            max="100"
            value={form.entryScore}
            onChange={(event) => updateForm({ entryScore: Number(event.target.value) })}
          />
        </label>
        <label>
          <span>退出分數</span>
          <input
            type="number"
            min="0"
            max="99"
            value={form.exitScore}
            onChange={(event) => updateForm({ exitScore: Number(event.target.value) })}
          />
        </label>
      </div>
      <label>
        <span>初始資金（NT$）</span>
        <input
          type="number"
          min="1"
          required
          value={form.initialCapital}
          onChange={(event) => updateForm({ initialCapital: Number(event.target.value) })}
        />
      </label>
      <div className="threshold-row">
        <label>
          <span>單邊費用（bps）</span>
          <input
            type="number"
            min="0"
            max="1000"
            step="0.01"
            required
            value={form.feeBps}
            onChange={(event) => updateForm({ feeBps: Number(event.target.value) })}
          />
        </label>
        <label>
          <span>單邊滑價（bps）</span>
          <input
            type="number"
            min="0"
            max="1000"
            step="0.01"
            required
            value={form.slippageBps}
            onChange={(event) => updateForm({ slippageBps: Number(event.target.value) })}
          />
        </label>
      </div>
      <label>
        <span>賣出稅費（bps）</span>
        <input
          type="number"
          min="0"
          max="1000"
          step="0.01"
          required
          value={form.sellTaxBps}
          onChange={(event) => updateForm({ sellTaxBps: Number(event.target.value) })}
        />
      </label>
      <div className="cost-note">
        1 bps = 0.01%。請依商品與券商填入成本；期末保留部位，未強制平倉。
      </div>
      <label className="chip-filter-toggle">
        <input
          type="checkbox"
          checked={form.useMarketFilter}
          onChange={(event) => updateForm({ useMarketFilter: event.target.checked })}
        />
        <span>
          啟用市場環境濾網<small>Risk-off 時退出持股</small>
        </span>
      </label>
      {!hasRealMarketData && (
        <p className="research-note">
          目前此市場尚無官方環境資料。啟用市場濾網會使用示範資料，結果僅供功能展示。
        </p>
      )}
      <label className="chip-filter-toggle">
        <input
          type="checkbox"
          checked={form.useChipFilter}
          onChange={(event) => updateForm({ useChipFilter: event.target.checked })}
        />
        <span>
          啟用籌碼濾網
          <small>籌碼分數 ≥55 才進場，低於 40 退出</small>
        </span>
      </label>
      <button
        className="primary-button full"
        disabled={
          loading ||
          form.exitScore >= form.entryScore ||
          !form.start ||
          form.start >= query.asOf
        }
      >
        {loading ? "正在執行回測…" : "啟動策略回測"}
      </button>
    </form>
  );
}
