import { InfoIcon } from "@/components/icons";
import { shortDate } from "@/lib/format";

/** Calm progress notice while the server is still downloading history. */
export function BackfillNotice() {
  return (
    <div className="notice notice-sync" role="status">
      <span className="spinner" aria-hidden="true" />
      <div className="notice-body">
        <strong>資料回補中</strong>
        <p>
          伺服器仍在下載歷史資料，排行與圖表可能暫時不完整。頁面每 15 秒會自動更新，不需要重新整理。
        </p>
      </div>
    </div>
  );
}

/** Quotes are out but the exchange has not published the institutional / credit report yet. */
export function AwaitingReportNotice({
  report,
  awaiting,
  through,
}: {
  report: string;
  awaiting: string;
  through: string | null;
}) {
  return (
    <p className="callout info" role="status">
      <InfoIcon size={16} />
      <span>
        {shortDate(awaiting)} {report}資料尚未公布
        {through ? `，顯示至 ${shortDate(through)}` : ""}。交易所通常在收盤後數小時才陸續公布，公布後重新整理即可納入。
      </span>
    </p>
  );
}

export function PartialHistoryNotice({ days, window }: { days: number; window: number }) {
  return (
    <p className="callout info" role="status">
      <InfoIcon size={16} />
      <span>
        目前僅 {days} 個交易日資料，不足 {window} 日；排行以現有 {days} 日加總計算。
      </span>
    </p>
  );
}
