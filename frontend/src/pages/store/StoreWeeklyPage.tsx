import { StoreReportView } from "@/features/report/store/StoreReportView";

/** 店铺周汇总（7e R4）：ISO 周 ∩ 所选区间，行上带实际起止。 */
export function StoreWeeklyPage() {
  return <StoreReportView granularity="week" />;
}
