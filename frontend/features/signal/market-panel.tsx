import { RegimeChip } from "@/components/status-chip";
import type { MarketCode } from "@/lib/api/endpoints";
import type { MarketEnvironment } from "@/lib/api/types";
import { marketFactorLabels } from "@/lib/copy";
import { formatNumber, formatPct } from "@/lib/format";

const isOfficial = (environment: MarketEnvironment | null) =>
  environment?.latest.source === "twse_official" ||
  environment?.latest.source === "tpex_official";

export function MarketPanel({
  market,
  environment,
}: {
  market: MarketCode;
  environment: MarketEnvironment | null;
}) {
  return (
    <section className="market-panel" aria-label="市場環境">
      <div className="market-score">
        <div>
          <p className="eyebrow">MARKET ENVIRONMENT</p>
          <span className="market-name">
            {market === "TWO" ? "上櫃市場 · TWO" : "上市市場 · TW"} ·{" "}
            {isOfficial(environment) ? "交易所官方資料" : "示範資料"}
          </span>
        </div>
        <strong>{environment ? formatNumber(environment.score, 0) : "—"}</strong>
        <RegimeChip regime={environment?.regime} />
      </div>
      <div className="market-factor-grid">
        {environment
          ? (
              Object.entries(environment.factors) as [
                keyof MarketEnvironment["factors"],
                number,
              ][]
            ).map(([key, value]) => (
              <div key={key}>
                <span>{marketFactorLabels[key]}</span>
                <strong>{formatNumber(value, 0)}</strong>
                <i aria-hidden="true">
                  <b style={{ width: `${value}%` }} />
                </i>
              </div>
            ))
          : Object.values(marketFactorLabels).map((label) => (
              <div className="muted" key={label}>
                <span>{label}</span>
                <strong>—</strong>
                <i aria-hidden="true" />
              </div>
            ))}
      </div>
      <div className="market-tape">
        <div>
          <span>上漲／下跌</span>
          <strong>
            {environment
              ? `${environment.latest.advancing_issues} / ${environment.latest.declining_issues}`
              : "—"}
          </strong>
        </div>
        <div>
          <span>MA20 以上</span>
          <strong>
            {environment
              ? formatPct(
                  environment.latest.above_ma20_issues / environment.latest.total_issues,
                )
              : "—"}
          </strong>
        </div>
        <div>
          <span>法人合計</span>
          <strong>
            {environment
              ? `${formatNumber(
                  (environment.latest.foreign_net_flow +
                    environment.latest.investment_trust_net_flow +
                    environment.latest.dealer_net_flow) /
                    100_000_000,
                  1,
                )} 億`
              : "—"}
          </strong>
        </div>
        <div>
          <span>期貨淨部位</span>
          <strong>
            {isOfficial(environment)
              ? "待串 TAIFEX"
              : environment
                ? formatNumber(environment.latest.futures_net_open_interest, 0)
                : "—"}
          </strong>
        </div>
        <div>
          <span>市場異常</span>
          <strong>{environment?.alerts.length ?? 0}</strong>
        </div>
      </div>
    </section>
  );
}
