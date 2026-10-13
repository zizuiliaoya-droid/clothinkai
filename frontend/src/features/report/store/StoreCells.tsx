// 店铺报表里要渲染 JSX 的格子（7e R4）：列定义在 ./storeColumns.tsx。
import type { StoreGranularity, StoreReportRow } from "../types";
import { pct, recordedDaysLabel } from "./storeFormat";

/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";

/** 仅退款率：周 / 月行下面多一行「已录 x / y 天」（日行只有 0 / 1 天，不写）。 */
export function RefundOnlyRateCell({
  row,
  granularity,
}: {
  row: StoreReportRow;
  granularity: StoreGranularity;
}) {
  if (granularity === "day") return <>{pct(row.refund_only_rate)}</>;
  return (
    <>
      <div>{pct(row.refund_only_rate)}</div>
      <div style={{ fontSize: 12, color: MUTED }}>
        {recordedDaysLabel(row.refund_only_days, row.day_count)}
      </div>
    </>
  );
}
