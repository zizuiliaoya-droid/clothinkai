import { useQueryClient } from "@tanstack/react-query";
import { message } from "antd";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { FREIGHT_PAYMENTS_KEY } from "../freightApi";

/** 409 只刷新能力，不关闭弹窗、不重置用户输入，也不重试付款。 */
export function useRecallFreightFeedback(lineId?: string | null) {
  const client = useQueryClient();
  const refresh = () => {
    for (const queryKey of [["promotions"], ["promotion-stage-counts"], lineId ? ["line-events", lineId] : ["line-events"], FREIGHT_PAYMENTS_KEY, ["settlements"]]) {
      void client.invalidateQueries({ queryKey });
    }
  };
  const handleError = (error: unknown) => {
    const outcome = flowErrorOutcome(error);
    if (outcome.kind === "gate") message.error(`缺：${outcome.missing.map((item) => item.label).join("、")}`);
    else {
      message.error(outcome.text);
      if (outcome.refresh) refresh();
    }
  };
  return { refresh, handleError };
}
