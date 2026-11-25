"use client";

import { useDashboard } from "@/lib/dashboard-context";
import { useScrollIntoViewOnStart } from "@/lib/use-scroll-into-view";
import { ChartIcon, CheckIcon, FlaskIcon } from "@/components/icons";
import { BacktestForm } from "./backtest-form";
import { BacktestResult } from "./backtest-result";

export function BacktestView() {
  const { backtest } = useDashboard();
  useScrollIntoViewOnStart(backtest.loading, "backtest-result");
  return (
    <>
      <section className="lab-heading" aria-labelledby="lab-title">
        <div>
          <p className="lab-eyebrow"><FlaskIcon size={16} /> Quant Signal Lab</p>
          <h1 id="lab-title">你的股票回測實驗室</h1>
          <p>組合交易條件、納入真實成本，並在互動圖表上檢查策略的每一段表現。</p>
        </div>
        <div className="lab-principles" aria-label="回測原則">
          <span><CheckIcon size={15} /> Point-in-time</span>
          <span><CheckIcon size={15} /> 隔日開盤成交</span>
          <span><CheckIcon size={15} /> 已計入交易成本</span>
        </div>
      </section>
      <ol className="lab-steps" aria-label="回測流程">
        <li data-active="true"><span>1</span><div><strong>設計策略</strong><small>門檻、資金與濾網</small></div></li>
        <li data-active={Boolean(backtest.job) || undefined}><span>2</span><div><strong>執行回測</strong><small>使用歷史可得資訊</small></div></li>
        <li data-active={Boolean(backtest.job?.result) || undefined}><span><ChartIcon size={15} /></span><div><strong>分析結果</strong><small>績效、風險與圖表標記</small></div></li>
      </ol>
      <div className="workbench">
        <BacktestForm />
        <div id="backtest-result" className="scroll-target">
          <BacktestResult />
        </div>
      </div>
    </>
  );
}
