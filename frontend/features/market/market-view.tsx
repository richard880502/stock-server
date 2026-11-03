"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { ErrorBanner } from "@/components/error-banner";
import { getMarketRankings } from "@/lib/api/endpoints";
import { useDashboard } from "@/lib/dashboard-context";
import { MarketControls } from "./market-controls";
import { AwaitingReportNotice, BackfillNotice, PartialHistoryNotice } from "./market-notices";
import { METRICS } from "./metrics";
import { RankingBoard } from "./ranking-board";
import { useCommittedAsOf } from "./use-committed-as-of";
import { useMarketParams } from "./use-market-params";
import { useMarketResource } from "./use-market-resource";

const LIMIT = 20;

export function MarketView() {
  const { query, refresh } = useDashboard();
  const router = useRouter();
  const { params, update } = useMarketParams();
  const asOf = useCommittedAsOf(query.asOf);
  const info = METRICS[params.metric];

  const rankingKey = (order: "desc" | "asc") =>
    `${order}|${params.metric}|${params.market}|${params.window}|${asOf}`;
  const loadRanking = (order: "desc" | "asc") => (signal: AbortSignal) =>
    getMarketRankings(
      { metric: params.metric, market: params.market, window: params.window, order, limit: LIMIT, asOf },
      { signal },
    );

  const top = useMarketResource(rankingKey("desc"), loadRanking("desc"));
  const bottom = useMarketResource(info.bottom ? rankingKey("asc") : null, loadRanking("asc"));

  const pick = useCallback(
    (symbol: string) => {
      refresh({ symbol });
      router.push("/");
    },
    [refresh, router],
  );

  const lead = top.data && top.data.metric === params.metric ? top.data : null;
  const backfill = Boolean(lead?.backfill_pending || bottom.data?.backfill_pending);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const error = top.error ?? bottom.error;
  const retry = () => {
    if (top.error) top.retry();
    if (bottom.error) bottom.retry();
  };

  return (
    <>
      <div className="view-heading">
        <h1>市場</h1>
        <p>看資金流向哪裡：法人買賣超、成交與信用排行，再對照整體資金趨勢。</p>
      </div>

      <MarketControls
        market={params.market}
        window={params.window}
        metric={params.metric}
        onMarket={(market) => update({ market })}
        onWindow={(window) => update({ window })}
        onMetric={(metric) => update({ metric })}
      />

      {backfill && <BackfillNotice />}
      {error && error !== dismissed && (
        <ErrorBanner message={error} onDismiss={() => setDismissed(error)} onRetry={retry} />
      )}
      {lead?.awaiting_report_date && info.report && (
        <AwaitingReportNotice
          report={info.report}
          awaiting={lead.awaiting_report_date}
          through={lead.data_through}
        />
      )}
      {lead && lead.trading_days > 0 && lead.trading_days < params.window && (
        <PartialHistoryNotice days={lead.trading_days} window={params.window} />
      )}

      <div className="board-grid">
        <RankingBoard
          title={info.top}
          metric={params.metric}
          scope={params.market}
          window={params.window}
          data={top.data}
          loading={top.loading}
          stale={top.stale}
          onPick={pick}
        />
        {info.bottom && (
          <RankingBoard
            title={info.bottom}
            metric={params.metric}
            scope={params.market}
            window={params.window}
            data={bottom.data}
            loading={bottom.loading}
            stale={bottom.stale}
            onPick={pick}
          />
        )}
      </div>
    </>
  );
}
