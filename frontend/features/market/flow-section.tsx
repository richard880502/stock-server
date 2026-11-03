"use client";

import { useMemo } from "react";
import { Card } from "@/components/card";
import { FlowChart, FlowLegend } from "@/components/charts/flow-chart";
import { BreadthChart, TrendChart } from "@/components/charts/trend-chart";
import { EmptyState } from "@/components/empty-state";
import { ChevronDownIcon, ClockIcon, InfoIcon } from "@/components/icons";
import { ResearchTable } from "@/components/research-table";
import { SegmentedControl } from "@/components/segmented-control";
import type { MarketFlowDay, MarketFlows, MarketScope } from "@/lib/api/types";
import {
  directionOf,
  formatLots,
  formatNumber,
  formatSigned,
  formatYi,
  shortDate,
} from "@/lib/format";
import { FLOW_RANGES, SCOPE_NAME } from "./metrics";

const RANGE_OPTIONS = FLOW_RANGES.map((value) => ({ value, label: `${value} 日` }));

function Skeleton() {
  return (
    <div aria-hidden="true" className="flow-skeleton">
      <span className="skeleton block" style={{ height: 260 }} />
      <div className="trend-grid">
        {[0, 1, 2].map((i) => (
          <span key={i} className="skeleton block" style={{ height: 200 }} />
        ))}
      </div>
    </div>
  );
}

const signedYi = (twd: number) => formatSigned(twd / 1e8, 1);
const toneClass = (value: number) =>
  directionOf(value) === "up" ? "text-up" : directionOf(value) === "down" ? "text-down" : "";

function MissingReportNotice({ days }: { days: MarketFlowDay[] }) {
  const noInstitutional = days.filter((d) => !d.institutional_reported);
  const noCredit = days.filter((d) => !d.credit_reported);
  if (noInstitutional.length === 0 && noCredit.length === 0) return null;
  const describe = (rows: MarketFlowDay[], name: string) =>
    rows.length === 1
      ? `${shortDate(rows[0].trading_date)} ${name}資料尚未公布`
      : `${rows.length} 個交易日（最近 ${shortDate(rows[rows.length - 1].trading_date)}）的${name}資料尚未公布`;
  const parts = [
    noInstitutional.length ? describe(noInstitutional, "三大法人") : null,
    noCredit.length ? describe(noCredit, "融資融券") : null,
  ].filter(Boolean);
  return (
    <p className="callout info" role="status">
      <InfoIcon size={16} />
      <span>
        {parts.join("；")}。圖表以斜線標示這些日子，不會當作 0。交易所通常在收盤後數小時才公布。
      </span>
    </p>
  );
}

function DailyTable({ days }: { days: MarketFlowDay[] }) {
  const rows = [...days].reverse();
  return (
    <ResearchTable
      label="每日資金流向明細"
      headers={[
        "日期",
        "外資（億）",
        "投信（億）",
        "自營商（億）",
        "法人合計（億）",
        "成交值（億）",
        "融資餘額（張）",
        "上漲",
        "下跌",
        "平盤",
      ]}
    >
      {rows.map((d) => {
        const total = d.foreign_net_value + d.trust_net_value + d.dealer_net_value;
        return (
          <tr key={d.trading_date}>
            <th scope="row">{d.trading_date}</th>
            {d.institutional_reported ? (
              <>
                {[d.foreign_net_value, d.trust_net_value, d.dealer_net_value, total].map((v, i) => (
                  <td key={i} className={toneClass(v)}>
                    {signedYi(v)}
                  </td>
                ))}
              </>
            ) : (
              <td colSpan={4} className="text-muted">
                尚未公布
              </td>
            )}
            <td>{formatYi(d.turnover_value, 0)}</td>
            <td className={d.credit_reported ? undefined : "text-muted"}>
              {d.credit_reported ? formatLots(d.margin_balance) : "尚未公布"}
            </td>
            <td>{formatLots(d.advancing)}</td>
            <td>{formatLots(d.declining)}</td>
            <td>{formatLots(d.unchanged)}</td>
          </tr>
        );
      })}
    </ResearchTable>
  );
}

function TrendCard({
  title,
  sub,
  latest,
  children,
}: {
  title: string;
  sub: string;
  latest: string;
  children: React.ReactNode;
}) {
  return (
    <Card title={title} sub={sub} aside={<strong className="num trend-latest">{latest}</strong>} className="trend-card">
      {children}
    </Card>
  );
}

export function FlowSection({
  scope,
  range,
  cumulative,
  onRange,
  onCumulative,
  data,
  loading,
  stale,
}: {
  scope: MarketScope;
  range: number;
  cumulative: boolean;
  onRange: (range: number) => void;
  onCumulative: (cumulative: boolean) => void;
  data: MarketFlows | null;
  loading: boolean;
  stale: boolean;
}) {
  const days = useMemo(() => data?.days ?? [], [data]);
  const latest = days[days.length - 1];
  const lastCredit = [...days].reverse().find((d) => d.credit_reported);

  return (
    <section className="flow-section" aria-labelledby="flow-heading">
      <div className="section-title">
        <h2 id="flow-heading">資金流向趨勢</h2>
        <p>
          {SCOPE_NAME[scope]}
          {days.length > 0 ? ` · ${shortDate(days[0].trading_date)}–${shortDate(days[days.length - 1].trading_date)}，共 ${days.length} 個交易日` : ""}
        </p>
      </div>

      {!data && loading ? (
        <Skeleton />
      ) : !data ? null : days.length === 0 ? (
        data.backfill_pending ? (
          <EmptyState icon={<ClockIcon size={22} />} title="資料回補中" live>
            歷史資料還在下載，完成前無法繪製趨勢。頁面會每 15 秒自動更新。
          </EmptyState>
        ) : (
          <EmptyState icon={<InfoIcon size={22} />} title="這個範圍還沒有資金流向資料">
            可以換一個市場或資料截止日再看看。
          </EmptyState>
        )
      ) : (
        <div className={`flow-body${stale || loading ? " is-refreshing" : ""}`} aria-busy={stale || loading}>
          <Card
            title="三大法人買賣超"
            sub="每日淨買賣超金額，外資、投信、自營商分開看"
            className="flow-card"
            aside={
              <div className="flow-controls">
                <label className="switch-row flow-cumulative">
                  <input
                    type="checkbox"
                    checked={cumulative}
                    onChange={(event) => onCumulative(event.target.checked)}
                  />
                  <span className="switch-copy">顯示累計</span>
                </label>
                <SegmentedControl label="顯示區間" value={range} options={RANGE_OPTIONS} onChange={onRange} />
              </div>
            }
          >
            <MissingReportNotice days={days} />
            <figure className="chart-figure">
              <FlowChart
                days={days}
                cumulative={cumulative}
                label={`${SCOPE_NAME[scope]}三大法人每日淨買賣超估算金額，${days[0].trading_date} 至 ${days[days.length - 1].trading_date}`}
              />
              <FlowLegend cumulative={cumulative} hasGap={days.some((d) => !d.institutional_reported)} />
              <figcaption className="estimate-note">
                <span className="chip plain" title={data.value_basis}>
                  估算
                </span>
                <span>
                  金額為估算值（淨買賣股數 × 當日收盤價），並非交易所公布的買賣超金額；排行榜則使用實際股數（張）。
                </span>
              </figcaption>
            </figure>
          </Card>

          <div className="trend-grid">
            <TrendCard
              title="成交值"
              sub="每日，億 NT$"
              latest={latest ? `${formatNumber(latest.turnover_value / 1e8, 0)} 億` : "—"}
            >
              <TrendChart
                variant="bars"
                label="每日成交值（億元）"
                seriesLabel="成交值"
                color="var(--series-strategy)"
                points={days.map((d) => ({ date: d.trading_date, value: d.turnover_value / 1e8 }))}
                format={(v) => `${formatNumber(v, 0)} 億`}
                axis={(v) => formatNumber(v, 0)}
                missingNote=""
              />
            </TrendCard>
            <TrendCard
              title="融資餘額"
              sub="每日，萬張"
              latest={lastCredit ? `${formatNumber(lastCredit.margin_balance / 1e4, 1)} 萬張` : "—"}
            >
              <TrendChart
                variant="line"
                label="每日融資餘額（萬張）"
                seriesLabel="融資餘額"
                color="var(--series-history)"
                points={days.map((d) => ({
                  date: d.trading_date,
                  value: d.credit_reported ? d.margin_balance : null,
                }))}
                format={(v) => `${formatLots(v)} 張`}
                axis={(v) => (v / 1e4).toLocaleString("zh-TW", { maximumFractionDigits: 1 })}
                missingNote="融資融券資料公布後才會補上，不視為 0"
              />
            </TrendCard>
            <TrendCard
              title="上漲 vs 下跌家數"
              sub="每日，檔"
              latest={latest ? `${formatLots(latest.advancing)} / ${formatLots(latest.declining)}` : "—"}
            >
              <BreadthChart
                label="每日上漲與下跌家數"
                points={days.map((d) => ({
                  date: d.trading_date,
                  advancing: d.advancing,
                  declining: d.declining,
                  unchanged: d.unchanged,
                }))}
              />
              <ul className="chart-legend breadth-legend" aria-label="圖例">
                <li>
                  <span className="text-up">▲ 上漲</span>
                </li>
                <li>
                  <span className="text-down">▼ 下跌</span>
                </li>
              </ul>
            </TrendCard>
          </div>

          <details className="disclosure chart-table">
            <summary>
              <span>以表格檢視每日資料</span>
              <ChevronDownIcon size={16} />
            </summary>
            <div className="disclosure-body">
              <DailyTable days={days} />
            </div>
          </details>
        </div>
      )}
    </section>
  );
}
