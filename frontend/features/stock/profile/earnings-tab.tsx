"use client";

import { ProfileChart } from "./charts/profile-chart";
import type { EarningsSection, RevenueSection, StockFundamentals } from "@/lib/api/profile";
import { formatNumber } from "@/lib/format";
import {
  emptyReason,
  formatPercent,
  formatSignedPercent,
  formatYiFromThousands,
  monthLabel,
  thousandsToYi,
  toneOf,
} from "./model";
import { Boundary, DataDate, EmptyLine, Notes, Tile, TileGrid } from "./parts";
import { useFundamentals } from "./valuation-tab";

/** Why the 3-/12-month year-on-year tiles are blank, in one line (null when they have values). */
export function revenueFactReason(r: RevenueSection): string | null {
  if (r.yoy_3m_pct !== null && r.yoy_12m_pct !== null) return null;
  const n = r.months.length;
  if (n < 15) return `近 12 個月年增需要至少 24 個月的月營收，目前只有 ${n} 個月，歷史逐月回補中。`;
  return "年增計算所需的月份有缺漏，暫不顯示。";
}

export function RevenueBlock({ revenue }: { revenue: RevenueSection | null }) {
  if (!revenue || revenue.months.length === 0) return <EmptyLine>{emptyReason("revenue")}</EmptyLine>;
  const months = revenue.months;
  const dates = months.map((m) => m.period);
  const reason = revenueFactReason(revenue);
  return (
    <section aria-label="月營收">
      <h3 className="pf-subtitle">月營收（億元，右軸為年增率）</h3>
      <DataDate>最新月份 {months[months.length - 1].period}・月營收為公告值，單位換算自新台幣仟元</DataDate>
      <TileGrid>
        <Tile label="近 3 個月年增" value={formatSignedPercent(revenue.yoy_3m_pct)} tone={toneOf(revenue.yoy_3m_pct)} />
        <Tile label="近 12 個月年增" value={formatSignedPercent(revenue.yoy_12m_pct)} tone={toneOf(revenue.yoy_12m_pct)} />
        <Tile
          label="連續成長月數"
          value={`${revenue.growth_streak_months} 個月`}
          note="連續幾個月年增率為正"
        />
        <Tile
          label="是否創 12 個月新高"
          value={revenue.is_12m_high === null ? "—" : revenue.is_12m_high ? "是" : "否"}
          note={revenue.is_12m_high === null ? "需要連續 12 個月的資料" : undefined}
        />
      </TileGrid>
      {reason && <p className="pf-reason">{reason}</p>}
      <ProfileChart
        title="月營收與年增率"
        dates={dates}
        xLabels={dates.map(monthLabel)}
        series={[
          {
            key: "revenue",
            label: "月營收（億）",
            kind: "bar",
            values: months.map((m) => thousandsToYi(m.revenue)),
            color: "var(--series-strategy)",
            format: (v) => `${formatNumber(v, 1)} 億`,
          },
          {
            key: "yoy",
            label: "年增率",
            kind: "line",
            axis: "right",
            values: months.map((m) => m.yoy_pct),
            color: "var(--series-ma5)",
            dash: "6 4",
            legendDash: true,
            format: (v) => formatSignedPercent(v) as string,
          },
        ]}
        tickFormat={(v) => formatNumber(v, v % 1 === 0 ? 0 : 1)}
        rightTickFormat={(v) => `${formatNumber(v, 0)}%`}
        extraRows={(i) => [{ label: "月增率", value: formatSignedPercent(months[i].mom_pct) }]}
        height={220}
      />
    </section>
  );
}

export function EarningsBlock({ earnings }: { earnings: EarningsSection | null }) {
  if (!earnings || earnings.quarters.length === 0) return <EmptyLine>{emptyReason("earnings")}</EmptyLine>;
  const quarters = earnings.quarters;
  const dates = quarters.map((q) => q.period);
  const reportedAny = quarters.some((q) => q.eps !== null || q.gross_margin_pct !== null || q.revenue !== null);
  return (
    <section aria-label="季度獲利">
      <h3 className="pf-subtitle">季度獲利（單季；EPS 為長條、毛利／營益／淨利率為折線）</h3>
      <TileGrid>
        <Tile label="近四季 EPS（TTM）" value={earnings.eps_ttm === null ? "—" : `${formatNumber(earnings.eps_ttm, 2)} 元`} />
        <Tile label="EPS 年增" value={formatSignedPercent(earnings.eps_yoy_pct)} tone={toneOf(earnings.eps_yoy_pct)} />
        <Tile label="ROE（近四季）" value={formatPercent(earnings.roe_ttm_pct)} note="以最新期末母公司權益計算" />
      </TileGrid>
      {earnings.eps_ttm === null && (
        <p className="pf-reason">近四季 EPS／ROE 需要連續四季財報，目前資料不足，隨財報累積補上。</p>
      )}
      {reportedAny && (
        <ProfileChart
          title="季度 EPS 與利潤率"
          dates={dates}
          series={[
            {
              key: "eps",
              label: "EPS（元）",
              kind: "bar",
              values: quarters.map((q) => q.eps),
              color: "var(--series-strategy)",
              toneBars: true,
              format: (v) => `${formatNumber(v, 2)} 元`,
            },
            {
              key: "gm",
              label: "毛利率",
              kind: "line",
              axis: "right",
              values: quarters.map((q) => q.gross_margin_pct),
              color: "var(--series-ma20)",
              format: (v) => formatPercent(v) as string,
            },
            {
              key: "om",
              label: "營益率",
              kind: "line",
              axis: "right",
              values: quarters.map((q) => q.operating_margin_pct),
              color: "var(--series-ma5)",
              dash: "6 4",
              legendDash: true,
              format: (v) => formatPercent(v) as string,
            },
            {
              key: "nm",
              label: "淨利率",
              kind: "line",
              axis: "right",
              values: quarters.map((q) => q.net_margin_pct),
              color: "var(--series-ma60)",
              dash: "1 4.5",
              legendDash: "dotted",
              format: (v) => formatPercent(v) as string,
            },
          ]}
          rightTickFormat={(v) => `${formatNumber(v, 0)}%`}
          tickFormat={(v) => formatNumber(v, v % 1 === 0 ? 0 : 1)}
          height={220}
        />
      )}
      <div className="pf-table-wrap">
        <table className="data-table pf-table">
          <caption className="sr-only">近 {quarters.length} 季損益摘要</caption>
          <thead>
            <tr>
              <th scope="col">季度</th>
              <th scope="col">營收（億）</th>
              <th scope="col">毛利率</th>
              <th scope="col">營益率</th>
              <th scope="col">淨利率</th>
              <th scope="col">EPS（元）</th>
            </tr>
          </thead>
          <tbody>
            {[...quarters].reverse().map((q) => (
              <tr key={q.period}>
                <th scope="row">{q.period}</th>
                <td className="num">{formatYiFromThousands(q.revenue).replace(" 億", "")}</td>
                <td className="num">{formatPercent(q.gross_margin_pct)}</td>
                <td className="num">{formatPercent(q.operating_margin_pct)}</td>
                <td className="num">{formatPercent(q.net_margin_pct)}</td>
                <td className={`num ${toneOf(q.eps)}`}>{q.eps === null ? "—" : formatNumber(q.eps, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!reportedAny && <p className="pf-reason">這些季度的單季數字還推算不出來（需要相鄰兩期的累計財報）。</p>}
    </section>
  );
}

export function EarningsBody({ data }: { data: StockFundamentals }) {
  return (
    <>
      <RevenueBlock revenue={data.revenue} />
      <EarningsBlock earnings={data.earnings} />
    </>
  );
}

export function EarningsTab({ symbol, asOf }: { symbol: string; asOf: string }) {
  const resource = useFundamentals(symbol, asOf);
  return (
    <Boundary resource={resource} label="營收獲利">
      {(data) => (
        <>
          <EarningsBody data={data} />
          <Notes notes={data.notes} />
        </>
      )}
    </Boundary>
  );
}
