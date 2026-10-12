import { StoreReportView } from "@/features/report/store/StoreReportView";

/** 店铺月度汇总（7e R4）：自然月 ∩ 所选区间，行上带实际起止。 */
export function StoreMonthlyPage() {
  return <StoreReportView granularity="month" />;
}
