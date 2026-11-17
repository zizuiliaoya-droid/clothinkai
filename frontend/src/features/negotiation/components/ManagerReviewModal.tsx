import { useEffect } from "react";
import { Form, Input, Modal, Radio, message, theme } from "antd";
import { useMutation } from "@tanstack/react-query";
import type { UiAction } from "@/features/flow/keys";
import { reviewNegotiation } from "../api";
import { MANAGER_REJECT_HINT } from "../negotiationDialogs";
import type { Negotiation, NegotiationReviewRequest } from "../types";
import { NegotiationSummary } from "./NegotiationSummary";
import { useNegotiationFeedback } from "./useNegotiationFeedback";
import { NegotiationRejectionHistory } from "./NegotiationRejectionHistory";

export interface NegotiationDialogTarget {
  row: Negotiation;
  /** 打开时的 `ui.actions[动作]`。 */
  action: UiAction;
}

type Props = {
  /** 菜单「主管审核」打开；null = 弹窗关着。 */
  target: NegotiationDialogTarget | null;
  onClose: () => void;
};

type Values = { decision: NegotiationReviewRequest["action"]; opinion?: string };

/** 主管审核：驳回后可在原单重提，历史原因始终从同一主线读取。 */
export function ManagerReviewModal({ target, onClose }: Props) {
  const { token } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const decision = Form.useWatch("decision", form);
  const { refresh, handleError } = useNegotiationFeedback();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: NegotiationReviewRequest }) =>
      reviewNegotiation(id, payload),
    onSuccess: (_, { payload }) => {
      message.success(payload.action === "approve" ? "已通过，等老板审核" : "已驳回，PR 可在 7 天内修改原单并重新提交");
      refresh();
      onClose();
    },
    onError: (err) => {
      handleError(err);
    },
  });

  const reject = decision === "reject";

  return (
    <Modal
      title="主管审核"
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText={reject ? "确认驳回" : "确认通过"}
      okButtonProps={{ danger: reject }}
      confirmLoading={mutation.isPending}
      destroyOnHidden
      width={560}
    >
      {target && <NegotiationSummary row={target.row} />}
      {target && <NegotiationRejectionHistory row={target.row} />}
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        initialValues={{ decision: "approve" }}
        onFinish={(v) => {
          if (!target) return;
          const opinion = v.opinion?.trim() || null;
          mutation.mutate({
            id: target.row.id,
            payload: { action: v.decision, review_opinion: opinion },
          });
        }}
      >
        <Form.Item name="decision" label="审核结果" rules={[{ required: true }]}>
          <Radio.Group
            options={[
              { label: "通过", value: "approve" },
              { label: "驳回", value: "reject" },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="opinion"
          label={reject ? "驳回意见" : "审核意见（可选）"}
          rules={reject ? [{ required: true, whitespace: true, message: "驳回要写意见" }] : []}
          extra={reject ? <span style={{ color: token.colorTextSecondary }}>{MANAGER_REJECT_HINT}</span> : undefined}
        >
          <Input.TextArea rows={3} maxLength={2000} placeholder="PR 会看到这段文字" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
