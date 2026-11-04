import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { InfoIcon } from "@/components/icons";
import { RegimeChip } from "@/components/status-chip";
import { Stat } from "@/components/stat";
import type { MarketCode } from "@/lib/api/endpoints";
import type { MarketEnvironment } from "@/lib/api/types";
import { marketFactorLabels } from "@/lib/copy";
import { scoreLine } from "@/lib/backfill";
import { useBackfillStatus } from "@/lib/use-backfill-status";
import { formatNumber, formatPct, formatSigned } from "@/lib/format";

const isOfficial = (environment: MarketEnvironment) =>
  environment.latest.source === "twse_official" || environment.latest.source === "tpex_official";

export function MarketPanel({
  market,
  environment,
  asOf,
}: {
  market: MarketCode;
  environment: MarketEnvironment;
  /** The date the user asked for; the market series may end earlier. */
  asOf: string;
}) {
  const { latest } = environment;
  const institutional =
    (latest.foreign_net_flow + latest.investment_trust_net_flow + latest.dealer_net_flow) / 1e8;
  const factors = Object.entries(environment.factors) as [
    keyof MarketEnvironment["factors"],
    number,
  ][];

  return (
    <Card
      title="市場環境"
      sub={`${market === "TWO" ? "上櫃市場" : "上市市場"} · ${
        isOfficial(environment) ? "交易所官方資料" : "示範資料"
      } · ${environment.as_of}`}
      aside={<RegimeChip regime={environment.regime} />}
      className="market-panel"
      aria-label="市場環境"
    >
      <div className="market-body">
        <div className="market-score">
          <span className="market-score-num num">{formatNumber(environment.score, 0)}</span>
          <span className="stat-label">
            大盤分數 / 100 · 信心 {formatPct(environment.confidence)}
          </span>
        </div>
        <ul className="market-factors">
          {factors.map(([key, value]) => (
            <li key={key}>
              <span className="factor-label">{marketFactorLabels[key]}</span>
              <div
                className="factor-track"
                role="img"
                aria-label={`${marketFactorLabels[key]} ${formatNumber(value, 0)} 分`}
              >
                <span className="factor-tick" />
                <span className="factor-fill" style={{ width: `${Math.max(1.5, value)}%` }} />
              </div>
              <span className="factor-value num">{formatNumber(value, 0)}</span>
            </li>
          ))}
        </ul>
      </div>
      {environment.as_of < asOf && (
        <p className="callout">
          <InfoIcon size={16} />
          <span>
            市場資料最新只到 {environment.as_of}，早於你選的截止日 {asOf}；上方為該日的市場環境。
            <ScoreRebuildNote market={market} />
          </span>
        </p>
      )}
      <div className="market-tape">
        <Stat
          label="上漲 / 下跌家數"
          value={
            <span className="tape-split">
              <Direction value={1}>{latest.advancing_issues}</Direction>
              <span className="text-muted">/</span>
              <Direction value={-1}>{latest.declining_issues}</Direction>
            </span>
          }
        />
        <Stat
          label="站上 MA20 比例"
          value={formatPct(latest.above_ma20_issues / latest.total_issues)}
        />
        <Stat
          label="法人合計買賣超"
          value={
            <Direction value={institutional}>{formatSigned(institutional, 1)} 億</Direction>
          }
        />
        <Stat
          label="期貨淨部位"
          value={isOfficial(environment) ? "尚未串接" : formatNumber(latest.futures_net_open_interest, 0)}
          note={isOfficial(environment) ? "待串 TAIFEX" : undefined}
        />
      </div>
    </Card>
  );
}

/**
 * Mounted only inside the "market data is older than your date" callout, so the
 * status is requested only when it is needed. Silent when the endpoint fails or the
 * score is current.
 */
function ScoreRebuildNote({ market }: { market: MarketCode }) {
  const { rows } = useBackfillStatus();
  const line = rows?.find((row) => row.market === market);
  const text = line ? scoreLine(line) : null;
  return text ? <span className="callout-extra">{text}</span> : null;
}

export function MarketSkeleton() {
  return (
    <section className="card" aria-busy="true" aria-label="市場環境載入中">
      <span className="skeleton line" style={{ width: "30%" }} />
      <span className="skeleton block" style={{ height: 120, marginTop: 16 }} />
    </section>
  );
}
