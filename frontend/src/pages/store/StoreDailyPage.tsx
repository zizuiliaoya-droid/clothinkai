import { StoreReportView } from "@/features/report/store/StoreReportView";

/** 店铺日明细（7e R4）。 */
export function StoreDailyPage() {
  return <StoreReportView granularity="day" />;
}
