"use client";

import { getStockShortInterest, type StockShortInterest } from "@/lib/api/profile";
import { formatNumber } from "@/lib/format";
import { daysUntil, daysUntilText, emptyReason, EVENT_KIND_LABEL, stockDividendText } from "./model";
import { Boundary, DataDate, EmptyLine, Notes } from "./parts";
import { profileKey, useProfileResource } from "./use-profile-resource";

const DAYS = 60;

/** Events come with the short-interest response: same cache entry as the 放空與當沖 tab. */
export function useShortInterest(symbol: string, asOf: string) {
  return useProfileResource<StockShortInterest>(profileKey("short", symbol, asOf, String(DAYS)), (signal) =>
    getStockShortInterest(symbol, asOf, DAYS, { signal }),
  );
}

export function EventsBody({ data }: { data: StockShortInterest }) {
  const events = [...data.upcoming_events].sort((a, b) => a.date.localeCompare(b.date));
  return (
    <>
      <DataDate>查詢日 {data.as_of}・交易所預告表，金額以公司公告為準</DataDate>
      {events.length === 0 ? (
        <EmptyLine>{emptyReason("events")}</EmptyLine>
      ) : (
        <div className="pf-table-wrap">
          <table className="data-table pf-table">
            <caption className="sr-only">未來 60 天的除權息</caption>
            <thead>
              <tr>
                <th scope="col">日期</th>
                <th scope="col">距查詢日</th>
                <th scope="col">類型</th>
                <th scope="col">現金股利</th>
                <th scope="col">股票股利</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={`${e.date}-${e.kind}`}>
                  <th scope="row" className="num">{e.date}</th>
                  <td>{daysUntilText(daysUntil(e.date, data.as_of))}</td>
                  <td>{EVENT_KIND_LABEL[e.kind]}</td>
                  <td className="num">{e.cash_dividend === null ? "尚未公告" : `${formatNumber(e.cash_dividend, 2)} 元/股`}</td>
                  <td className="num">{e.stock_dividend === null ? "尚未公告" : stockDividendText(e.stock_dividend)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Notes notes={data.notes.filter((n) => n.includes("除權息"))} />
    </>
  );
}

export function EventsTab({ symbol, asOf }: { symbol: string; asOf: string }) {
  const resource = useShortInterest(symbol, asOf);
  return (
    <Boundary resource={resource} label="事件">
      {(data) => <EventsBody data={data} />}
    </Boundary>
  );
}
