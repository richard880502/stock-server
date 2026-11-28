"use client";

import { Card } from "@/components/card";
import { RegimeChip } from "@/components/status-chip";
import { getMarketEnvironment, type MarketCode } from "@/lib/api/endpoints";
import { getMarketDerivatives, type MarketDerivatives } from "@/lib/api/profile";
import type { MarketEnvironment } from "@/lib/api/types";
import { regimeGloss, severityCopy } from "@/lib/copy";
import { formatNumber, shortDate, today } from "@/lib/format";
import { usePolled } from "@/features/paper/use-paper-data";
import { derivativeHeadlines, type Headline, topAlerts } from "./today-model";
import { CardError, CardSkeleton, MoreLink } from "./today-parts";

const MARKET_NAME: Record<MarketCode, string> = { TW: "上市", TWO: "上櫃" };

export function EnvironmentBody({ environment }: { environment: MarketEnvironment }) {
  const alerts = topAlerts(environment, 2);
  return (
    <>
      <div className="today-score">
        <strong className="today-score-num num">{formatNumber(environment.score, 0)}</strong>
        <span className="today-score-unit">/ 100</span>
        <span className="today-score-gloss">{regimeGloss[environment.regime]}</span>
      </div>
      <p className="today-meta">資料日期 {environment.as_of}</p>
      {alerts.length > 0 && (
        <ul className="today-alerts" aria-label="市場警示">
          {alerts.map((alert) => (
            <li key={alert.code} className={`today-alert sev-${alert.severity}`}>
              <span className="today-alert-sev">{severityCopy[alert.severity]}</span>
              <span>{alert.title}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function EnvironmentCard({ market }: { market: MarketCode }) {
  const asOf = today();
  const resource = usePolled(`today-env|${market}|${asOf}`, (signal) =>
    getMarketEnvironment(market, asOf, { signal }),
  );
  const data = resource.data;
  return (
    <Card
      title={`${MARKET_NAME[market]}市場環境`}
      aside={data ? <RegimeChip regime={data.regime} /> : undefined}
      className="today-card today-env"
    >
      {resource.error ? (
        <CardError message={resource.error.message} onRetry={resource.reload} />
      ) : !data ? (
        <CardSkeleton label={`${MARKET_NAME[market]}市場環境載入中`} />
      ) : (
        <EnvironmentBody environment={data} />
      )}
      <MoreLink href="/market">看市場 →</MoreLink>
    </Card>
  );
}

export function HeadlineList({ items }: { items: Headline[] }) {
  return (
    <dl className="today-headlines">
      {items.map((item) => (
        <div key={item.key} className="today-headline">
          <dt>{item.label}</dt>
          <dd className={`num ${item.tone === "flat" ? "" : `text-${item.tone}`}`}>{item.value}</dd>
          <dd className="today-headline-note">{item.note}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DerivativesBody({ data }: { data: MarketDerivatives }) {
  const hasAny = (data.days?.length ?? 0) > 0;
  if (!hasAny) {
    return <p className="note">期交所資料尚未就緒，收盤後更新。</p>;
  }
  return <HeadlineList items={derivativeHeadlines(data)} />;
}

function DerivativesCard() {
  const resource = usePolled("today-derivatives", (signal) => getMarketDerivatives(null, 10, { signal }));
  const data = resource.data;
  const through = data?.data_through ?? data?.latest?.date ?? null;
  return (
    <Card
      title="期貨與選擇權"
      sub={through ? `資料至 ${shortDate(through)}` : undefined}
      className="today-card today-deriv"
    >
      {resource.error ? (
        <CardError message={resource.error.message} onRetry={resource.reload} />
      ) : !data ? (
        <CardSkeleton label="期貨與選擇權載入中" />
      ) : (
        <DerivativesBody data={data} />
      )}
      <MoreLink href="/market">看市場 →</MoreLink>
    </Card>
  );
}

export function MarketCards() {
  return (
    <section className="today-markets" aria-label="大盤">
      <EnvironmentCard market="TW" />
      <EnvironmentCard market="TWO" />
      <DerivativesCard />
    </section>
  );
}

