"use client";

import Link from "next/link";
import { Card } from "@/components/card";
import type { OrderSide, Position } from "@/lib/api/paper";
import { formatPrice } from "@/lib/format";
import { stockHref } from "@/lib/stock-routes";
import {
  formatMoney,
  formatPercent,
  formatSignedMoney,
  formatSignedPercent,
  toneClass,
} from "./paper-math";

const code = (symbol: string) => symbol.replace(/\.TWO?$/, "");

export function PositionsTable({
  positions,
  loading,
  stale,
  onTrade,
}: {
  positions: Position[] | null;
  loading: boolean;
  stale: boolean;
  onTrade: (position: Position, side: OrderSide) => void;
}) {
  return (
    <Card title="持股" sub={positions ? `${positions.length} 檔` : undefined} className="paper-positions">
      {positions === null && loading ? (
        <div className="paper-skeleton-rows" aria-busy="true" aria-label="載入持股">
          {[0, 1, 2].map((i) => (
            <span key={i} className="skeleton block" style={{ height: "2.25rem" }} />
          ))}
        </div>
      ) : !positions || positions.length === 0 ? (
        <div className="paper-empty">
          <strong>還沒有持股</strong>
          <p>用右側的下單區買進第一檔，或從「選股」、「自選股」挑一檔再來。</p>
        </div>
      ) : (
        <div className={`table-wrap paper-table-wrap${stale ? " is-refreshing" : ""}`} tabIndex={0} role="region" aria-label="持股明細（可橫向捲動）">
          <table className="data-table paper-table paper-positions-table">
            <thead>
              <tr>
                <th scope="col">股票</th>
                <th scope="col">張數</th>
                <th scope="col">均價</th>
                <th scope="col">現價</th>
                <th scope="col">今日</th>
                <th scope="col">市值</th>
                <th scope="col">未實現損益</th>
                <th scope="col">佔比</th>
                <th scope="col"><span className="sr-only">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.symbol}>
                  <th scope="row" className="paper-stock-cell">
                    <Link href={stockHref(position.symbol)} className="paper-link" title="開啟個股頁">
                      <span className="paper-stock-name">{position.name}</span>
                      <span className="paper-stock-code num">{code(position.symbol)}</span>
                    </Link>
                  </th>
                  <td className="num">
                    {position.lots}
                    {position.available_shares !== position.shares && (
                      <small className="paper-sub">可賣 {Math.floor(position.available_shares / 1000)}</small>
                    )}
                  </td>
                  <td className="num">{formatPrice(position.avg_cost)}</td>
                  <td className="num">
                    {formatPrice(position.price)}
                    {position.price_delayed && (
                      <small className="paper-delay" title="此檔未在即時報價名額內，價格約有數分鐘延遲">
                        延遲
                      </small>
                    )}
                  </td>
                  <td className={`num ${toneClass(position.day_change_pct)}`}>
                    {position.day_change_pct == null ? "—" : formatSignedPercent(position.day_change_pct)}
                  </td>
                  <td className="num">{formatMoney(position.market_value)}</td>
                  <td className={`num ${toneClass(position.unrealized_pnl)}`}>
                    {formatSignedMoney(position.unrealized_pnl)}
                    <small className="paper-sub">{formatSignedPercent(position.unrealized_pct)}</small>
                  </td>
                  <td className="num paper-weight">
                    <span className="paper-weight-bar" aria-hidden="true">
                      <span style={{ width: `${Math.min(100, Math.max(0, position.weight_pct))}%` }} />
                    </span>
                    {formatPercent(position.weight_pct)}
                  </td>
                  <td className="paper-row-actions">
                    <button
                      type="button"
                      className="button small paper-buy"
                      onClick={() => onTrade(position, "buy")}
                      aria-label={`加碼 ${position.name}`}
                    >
                      加碼
                    </button>
                    <button
                      type="button"
                      className="button small paper-sell"
                      onClick={() => onTrade(position, "sell")}
                      disabled={position.available_shares < 1000}
                      aria-label={`賣出 ${position.name}`}
                    >
                      賣出
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
