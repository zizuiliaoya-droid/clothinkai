// 谈款弹窗共用的成功 / 失败处理（流程线设计 8.2）：刷新列表与计数；422 缺项交回弹窗标红，其余弹 message。
import { Modal, message } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import type { GateMissingItem } from "@/features/flow/keys";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { selfReviewNotice } from "../negotiationDialogs";
import type { Negotiation } from "../types";

export function useNegotiationFeedback() {
  const qc = useQueryClient();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["negotiations"] });
    void qc.invalidateQueries({ queryKey: ["negotiation-status-counts"] });
  };

  /** 422 缺项 → 返回 missing 交给弹窗标红；其余弹 message（403 缺 scope / 409 顺带刷新），返回 null。 */
  const handleError = (err: unknown): GateMissingItem[] | null => {
    const outcome = flowErrorOutcome(err);
    if (outcome.kind === "gate") return outcome.missing;
    message.error(outcome.text);
    if (outcome.refresh) refresh();
    return null;
  };

  /** 提交成功：主管自建的谈款另提示找管理员临时授权代理人（11-24）。 */
  const notifySubmitted = (saved: Negotiation) => {
    message.success("已提交，等 PR 主管审核");
    const notice = selfReviewNotice(saved);
    if (notice) Modal.info({ title: "需要代理人审核第一级", content: notice, okText: "知道了" });
  };

  return { refresh, handleError, notifySubmitted };
}
