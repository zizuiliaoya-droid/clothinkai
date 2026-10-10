// 推广单流程弹窗共用的成功 / 失败处理（流程线设计 8.2）。
import { message } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import type { GateMissingItem } from "@/features/flow/keys";
import { flowErrorOutcome } from "@/features/flow/flowError";

export function useFlowFeedback() {
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["promotions"] });

  /** 422 缺项 → 返回 missing 交给弹窗标红；其余弹 message（403 缺 scope / 409 顺带刷新），返回 null。 */
  const handleError = (err: unknown): GateMissingItem[] | null => {
    const outcome = flowErrorOutcome(err);
    if (outcome.kind === "gate") return outcome.missing;
    message.error(outcome.text);
    if (outcome.refresh) refresh();
    return null;
  };

  return { refresh, handleError };
}
