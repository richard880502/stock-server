"use client";

import { useState } from "react";
import { Card } from "@/components/card";
import { EmptyState } from "@/components/empty-state";
import { ClockIcon, InfoIcon } from "@/components/icons";
import { SegmentedControl } from "@/components/segmented-control";
import { ProfileChart } from "@/features/stock/profile/charts/profile-chart";
import { formatSignedInt, toneOf } from "@/features/stock/profile/model";
import { Notes, PanelError, Tile, TileGrid } from "@/features/stock/profile/parts";
import { profileKey, useProfileResource } from "@/features/stock/profile/use-profile-resource";
import "@/features/stock/profile/profile.css";
import { getMarketDerivatives, type MarketDerivatives } from "@/lib/api/profile";
import { formatNumber, formatSigned } from "@/lib/format";
import {
  DERIVATIVES_RANGES,
  type DerivativesRange,
  derivativesEmptyReason,
  lastDays,
  latestValue,
  positionWord,
  strikeFacts,
} from "./derivatives-model";

const FETCH_DAYS = 60;
const RANGE_OPTIONS = DERIVATIVES_RANGES.map((value) => ({ value, label: `${value} 日` }));
const kTick = (v: number) => (v === 0 ? "0" : `${formatNumber(v / 1000, 0)}k`);
const int = (v: number) => Math.round(v).toLocaleString("zh-TW");

export function DerivativesBody({ data, range }: { data: MarketDerivatives; range: DerivativesRange }) {
  const days = lastDays(data.days, range);
  const dates = days.map((d) => d.date);
  const foreign = latestValue(days, (d) => d.foreign_tx_equiv_net_oi);
  const change = latestValue(days, (d) => d.foreign_net_oi_change);
  const pc = latestValue(days, (d) => d.pc_oi_ratio);
  const top10 = latestValue(days, (d) => d.top10_net_oi);
  const top5 = latestValue(days, (d) => d.top5_net_oi);
  const vix = latestValue(days, (d) => d.vix);
  const latest = data.latest;
  const strikes = strikeFacts(latest);
  const single = days.length < 3 ? "資料才剛開始累積，趨勢要再等幾個交易日。" : null;

  return (
    <div className="pf-market-tiles">
      <div className="pf-market-tile wide">
        <TileGrid>
          <Tile
            label="外資台指期淨未平倉（大台等值，口）"
            value={foreign ? <span className={toneOf(foreign.value)}>{formatSignedInt(foreign.value)}</span> : "—"}
            note={foreign ? `${positionWord(foreign.value)}・${foreign.date}` : "尚無資料"}
          />
          <Tile
            label="較前一日"
            value={change ? <span className={toneOf(change.value)}>{formatSigned(change.value, 0)}</span> : "—"}
            note="口（大台等值）"
          />
          <Tile
            label="投信／自營商台指期淨部位"
            value={
              latest && latest.trust_tx_net_oi !== null ? (
                <span className={toneOf(latest.trust_tx_net_oi)}>{formatSignedInt(latest.trust_tx_net_oi)}</span>
              ) : (
                "—"
              )
            }
            note={latest?.dealer_tx_net_oi != null ? `自營商 ${formatSignedInt(latest.dealer_tx_net_oi)} 口` : "自營商：—"}
          />
        </TileGrid>
        <ProfileChart
          title="外資台指期淨未平倉（大台等值）"
          dates={dates}
          series={[
            {
              key: "net",
              label: "淨未平倉（口）",
              kind: "bar",
              values: days.map((d) => d.foreign_tx_equiv_net_oi),
              color: "var(--series-baseline)",
              toneBars: true,
              format: (v) => `${formatSignedInt(v)} 口`,
            },
          ]}
          includeZero
          tickFormat={(v) => kTick(v)}
          extraRows={(i) => [{ label: "較前一日", value: days[i].foreign_net_oi_change === null ? "—" : `${formatSigned(days[i].foreign_net_oi_change as number, 0)} 口` }]}
          note="多單為正（紅）、空單為負（綠）"
          hideLegend
          height={210}
        />
        {single && <p className="pf-reason">{single}</p>}
      </div>

      <div className="pf-market-tile">
        <Tile
          label="選擇權 Put/Call 未平倉比"
          value={pc ? `${formatNumber(pc.value, 1)}%` : "—"}
          note={
            pc
              ? `${pc.date}・賣權未平倉 ÷ 買權未平倉${latest?.pc_volume_ratio != null ? `；成交量比 ${formatNumber(latest.pc_volume_ratio, 1)}%` : ""}`
              : "尚無資料"
          }
        />
        <ProfileChart
          title="Put/Call 未平倉比"
          dates={dates}
          series={[
            { key: "pc", label: "未平倉比 %", kind: "line", values: days.map((d) => d.pc_oi_ratio), color: "var(--series-strategy)", format: (v) => `${formatNumber(v, 1)}%` },
          ]}
          tickFormat={(v) => `${formatNumber(v, 0)}%`}
          markLast
          hideLegend
          height={150}
        />
        {latest && (latest.foreign_call_net_oi !== null || latest.foreign_put_net_oi !== null) && (
          <p className="pf-reason">
            外資買權淨未平倉 {formatSignedInt(latest.foreign_call_net_oi)} 口、賣權 {formatSignedInt(latest.foreign_put_net_oi)} 口。
          </p>
        )}
      </div>

      <div className="pf-market-tile">
        <Tile
          label="前十大交易人台指期淨部位"
          value={top10 ? <span className={toneOf(top10.value)}>{formatSignedInt(top10.value)}</span> : "—"}
          note={top10 ? `${positionWord(top10.value)}${top5 ? `・前五大 ${formatSignedInt(top5.value)}` : ""}（口，全部月份）` : "尚無資料"}
        />
        <ProfileChart
          title="前十大交易人淨部位"
          dates={dates}
          series={[
            { key: "top10", label: "前十大", kind: "line", values: days.map((d) => d.top10_net_oi), color: "var(--series-strategy)", format: (v) => `${formatSignedInt(v)} 口` },
            { key: "top5", label: "前五大", kind: "line", values: days.map((d) => d.top5_net_oi), color: "var(--series-ma5)", dash: "6 4", legendDash: true, format: (v) => `${formatSignedInt(v)} 口` },
          ]}
          includeZero
          tickFormat={(v) => kTick(v)}
          height={150}
        />
      </div>

      <div className="pf-market-tile">
        <Tile
          label="臺指選擇權波動率指數 VIX"
          value={vix ? formatNumber(vix.value, 2) : "—"}
          note={vix ? `${vix.date}・13:45 收盤值` : "尚無資料"}
        />
        <ProfileChart
          title="VIX"
          dates={dates}
          series={[{ key: "vix", label: "VIX", kind: "line", values: days.map((d) => d.vix), color: "var(--series-ma60)", format: (v) => formatNumber(v, 2) }]}
          tickFormat={(v) => formatNumber(v, 0)}
          markLast
          hideLegend
          height={150}
        />
      </div>

      <div className="pf-market-tile">
        <Tile label="未平倉最大履約價" value={strikes.length ? "" : "—"} note={strikes.length ? "近月與週選臺指選擇權，僅描述未平倉分布，不是預測。" : "最大未平倉履約價自啟用日起逐日累積，尚無資料。"} />
        {strikes.length > 0 && (
          <div className="pf-strikes">
            {strikes.map((s) => (
              <Tile key={s.key} label={s.label} value={int(s.strike)} note={`${s.role}${s.expiry ? `・${s.expiry}` : ""}`} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** 大盤「期貨與選擇權」: foreign index-futures positioning, options ratios, large traders, VIX. */
export function DerivativesSection({ asOf }: { asOf: string }) {
  const [range, setRange] = useState<DerivativesRange>(20);
  const resource = useProfileResource<MarketDerivatives>(profileKey("market-derivatives", "TW", asOf, String(FETCH_DAYS)), (signal) =>
    getMarketDerivatives(asOf, FETCH_DAYS, { signal }),
  );
  const data = resource.data;
  const reason = data ? derivativesEmptyReason(data.data_through, data.days.length) : null;

  return (
    <section className="flow-section" aria-labelledby="derivatives-heading">
      <div className="section-title">
        <h2 id="derivatives-heading">期貨與選擇權</h2>
        <p>
          期交所官方日報表：外資台指期部位、選擇權多空比、大額交易人與波動率；皆為收盤後公布的事實，
          {data?.data_through ? `資料日 ${data.data_through}。` : "資料日待回補。"}
        </p>
      </div>
      {resource.error ? (
        <PanelError message={resource.error} onRetry={resource.retry} />
      ) : !data ? (
        <div aria-hidden="true" className="pf-market-tiles">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="skeleton block" style={{ height: 220 }} />
          ))}
        </div>
      ) : reason ? (
        <EmptyState icon={data.data_through ? <InfoIcon size={22} /> : <ClockIcon size={22} />} title="期貨與選擇權資料尚未就緒">
          {reason}
        </EmptyState>
      ) : (
        <Card
          title="三大法人與大額交易人部位"
          sub="口數單位；紅色代表淨多單、綠色代表淨空單"
          aside={<SegmentedControl label="期貨選擇權圖表區間" value={range} options={RANGE_OPTIONS} onChange={setRange} />}
        >
          <DerivativesBody data={data} range={range} />
          <Notes notes={data.notes} />
        </Card>
      )}
    </section>
  );
}
