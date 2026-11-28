"use client";

import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { dataTimeLabel } from "@/features/market/sector-math";
import { usePolled } from "@/features/paper/use-paper-data";
import { getMarketSectors } from "@/lib/api/endpoints";
import type { SectorRotation } from "@/lib/api/types";
import { formatSigned } from "@/lib/format";
import { extremeSectors, type SectorPick } from "./today-model";
import { CardError, CardSkeleton, MoreLink } from "./today-parts";

const WINDOW = 5;

function PickList({ title, picks }: { title: string; picks: SectorPick[] }) {
  return (
    <div className="today-sector-col">
      <h3 className="today-subhead">{title}</h3>
      <ul className="today-rows">
        {picks.map((pick) => (
          <li key={pick.code} className="today-sector">
            <span className="today-name">{pick.name}</span>
            <Direction value={pick.ret} className="num">
              {formatSigned(pick.ret, 2)}%
            </Direction>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SectorBody({ data }: { data: SectorRotation }) {
  const { strongest, weakest } = extremeSectors(data.sectors, WINDOW, 3);
  if (strongest.length === 0) return <p className="note">族群資料尚未就緒，收盤後更新。</p>;
  return (
    <div className="today-sectors-cols">
      <PickList title="近 5 日最強" picks={strongest} />
      <PickList title="近 5 日最弱" picks={weakest} />
    </div>
  );
}

export function SectorsCard() {
  const resource = usePolled(`today-sectors|${WINDOW}`, (signal) => getMarketSectors({ window: WINDOW }, { signal }));
  const data = resource.data;
  return (
    <Card
      title="族群輪動"
      sub={data ? `${WINDOW} 日等權漲跌 · ${dataTimeLabel(data)}` : `${WINDOW} 日等權漲跌`}
      className="today-card today-sectors"
    >
      <div className="today-body">
        {resource.error ? (
          <CardError message={resource.error.message} onRetry={resource.reload} />
        ) : !data ? (
          <CardSkeleton label="族群輪動載入中" lines={4} />
        ) : (
          <SectorBody data={data} />
        )}
      </div>
      <MoreLink href="/market">看族群輪動 →</MoreLink>
    </Card>
  );
}
