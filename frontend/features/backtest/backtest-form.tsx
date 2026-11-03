"use client";

import { Card } from "@/components/card";
import { ChevronDownIcon } from "@/components/icons";
import { useDashboard } from "@/lib/dashboard-context";

function NumberField({
  label,
  hint,
  ...props
}: {
  label: string;
  hint?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input className="input num" type="number" inputMode="decimal" {...props} />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function BacktestForm() {
  const { query, dataStatus, backtest } = useDashboard();
  const { form, updateForm, loading, run } = backtest;
  const hasRealMarketData = dataStatus?.market_series.some(
    (series) => series.key === query.market && series.is_real,
  );
  const invalidThreshold = form.exitScore >= form.entryScore;
  const invalidRange = !form.start || form.start >= query.asOf;
  const filterCount = Number(form.useMarketFilter) + Number(form.useChipFilter);

  return (
    <Card
      as="div"
      title="回測設定"
      sub={`${query.symbol} · 截止 ${query.asOf}`}
      className="backtest-form-card"
    >
      <form className="form-stack" onSubmit={run}>
        <label className="field">
          <span className="field-label">回測起始日</span>
          <input
            className="input"
            type="date"
            value={form.start}
            max={query.asOf}
            onChange={(event) => updateForm({ start: event.target.value })}
            aria-invalid={invalidRange || undefined}
          />
          {invalidRange && <span className="field-hint">起始日必須早於資料截止日。</span>}
        </label>

        <fieldset className="field-row">
          <legend className="field-label">進出場門檻（綜合分數）</legend>
          <NumberField
            label="進場 ≥"
            min="1"
            max="100"
            value={form.entryScore}
            onChange={(event) => updateForm({ entryScore: Number(event.target.value) })}
          />
          <NumberField
            label="退出 <"
            min="0"
            max="99"
            value={form.exitScore}
            onChange={(event) => updateForm({ exitScore: Number(event.target.value) })}
            aria-invalid={invalidThreshold || undefined}
          />
          {invalidThreshold && (
            <p className="field-hint field-row-full">退出分數必須低於進場分數。</p>
          )}
        </fieldset>

        <details className="disclosure">
          <summary>
            <span>
              資金與成本
              <small className="summary-meta num">
                {" "}
                · 費用 {form.feeBps} · 滑價 {form.slippageBps} · 稅 {form.sellTaxBps} bps
              </small>
            </span>
            <ChevronDownIcon size={16} />
          </summary>
          <div className="disclosure-body form-stack">
            <NumberField
              label="初始資金（NT$）"
              min="1"
              required
              value={form.initialCapital}
              onChange={(event) => updateForm({ initialCapital: Number(event.target.value) })}
            />
            <div className="field-row">
              <NumberField
                label="單邊費用（bps）"
                min="0"
                max="1000"
                step="0.01"
                required
                value={form.feeBps}
                onChange={(event) => updateForm({ feeBps: Number(event.target.value) })}
              />
              <NumberField
                label="單邊滑價（bps）"
                min="0"
                max="1000"
                step="0.01"
                required
                value={form.slippageBps}
                onChange={(event) => updateForm({ slippageBps: Number(event.target.value) })}
              />
            </div>
            <NumberField
              label="賣出稅費（bps）"
              min="0"
              max="1000"
              step="0.01"
              required
              value={form.sellTaxBps}
              onChange={(event) => updateForm({ sellTaxBps: Number(event.target.value) })}
            />
            <p className="note">
              1 bps = 0.01%。請依商品與券商填入成本；期末保留部位，未強制平倉。
            </p>
          </div>
        </details>

        <details className="disclosure">
          <summary>
            <span>
              濾網
              <small className="summary-meta">
                {" "}
                · {filterCount ? `已啟用 ${filterCount} 項` : "未啟用"}
              </small>
            </span>
            <ChevronDownIcon size={16} />
          </summary>
          <div className="disclosure-body form-stack">
            <label className="switch-row">
              <input
                type="checkbox"
                checked={form.useMarketFilter}
                onChange={(event) => updateForm({ useMarketFilter: event.target.checked })}
              />
              <span className="switch-copy">
                啟用市場環境濾網
                <small>Risk-off 時退出持股</small>
              </span>
            </label>
            {form.useMarketFilter && !hasRealMarketData && (
              <p className="callout">
                目前此市場尚無官方環境資料。啟用市場濾網會使用示範資料，結果僅供功能展示。
              </p>
            )}
            <label className="switch-row">
              <input
                type="checkbox"
                checked={form.useChipFilter}
                onChange={(event) => updateForm({ useChipFilter: event.target.checked })}
              />
              <span className="switch-copy">
                啟用籌碼濾網
                <small>籌碼分數 ≥55 才進場，低於 40 退出</small>
              </span>
            </label>
          </div>
        </details>

        <button
          className="button primary block"
          disabled={loading || invalidThreshold || invalidRange}
        >
          {loading ? (
            <>
              <span className="spinner small" aria-hidden="true" />
              正在執行回測
            </>
          ) : (
            "啟動策略回測"
          )}
        </button>
        <p className="note">
          以前一日收盤資訊產生訊號，下一交易日開盤成交，並計入費用與滑價。
        </p>
      </form>
    </Card>
  );
}
