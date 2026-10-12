import { useEffect } from "react";
import { Alert, Form, Input, Modal, Radio, message, theme } from "antd";
import { useMutation } from "@tanstack/react-query";
import { finalReviewNegotiation } from "../api";
import { FINAL_RETURN_HINT } from "../negotiationDialogs";
import type { NegotiationFinalReviewRequest } from "../types";
import type { NegotiationDialogTarget } from "./ManagerReviewModal";
import { NegotiationSummary } from "./NegotiationSummary";
import { useNegotiationFeedback } from "./useNegotiationFeedback";

type Props = {
  /** 菜单「老板审核」打开；null = 弹窗关着。 */
  target: NegotiationDialogTarget | null;
  onClose: () => void;
};

type Values = { decision: NegotiationFinalReviewRequest["action"]; opinion?: string };

/** 老板审核（N6 定稿 → 待补收货地址；N7 退回 → 老板退回，意见必填）。 */
export function FinalReviewModal({ target, onClose }: Props) {
  const { token } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const decision = Form.useWatch("decision", form);
  const { refresh, handleError } = useNegotiationFeedback();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: NegotiationFinalReviewRequest }) =>
      finalReviewNegotiation(id, payload),
    onSuccess: (_, { payload }) => {
      message.success(payload.action === "approve" ? "已定稿，等 PR 录收货地址" : "已退回，等 PR 改完重提");
      refresh();
      onClose();
    },
    onError: (err) => {
      handleError(err);
    },
  });

  const returning = decision === "return";
  const row = target?.row;

  return (
    <Modal
      title="老板审核"
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText={returning ? "确认退回" : "确认定稿"}
      okButtonProps={{ danger: returning }}
      confirmLoading={mutation.isPending}
      destroyOnHidden
      width={560}
    >
      {row && <NegotiationSummary row={row} />}
      {row?.review_opinion && (
        <Alert
          type="info"
          style={{ marginTop: 12 }}
          message={`主管意见（${row.reviewer_name ?? "PR 主管"}）`}
          description={row.review_opinion}
        />
      )}
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        initialValues={{ decision: "approve" }}
        onFinish={(v) => {
          if (!target) return;
          mutation.mutate({
            id: target.row.id,
            payload: { action: v.decision, opinion: v.opinion?.trim() || null },
          });
        }}
      >
        <Form.Item name="decision" label="审核结果" rules={[{ required: true }]}>
          <Radio.Group
            options={[
              { label: "定稿", value: "approve" },
              { label: "退回", value: "return" },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="opinion"
          label={returning ? "退回意见" : "意见（可选）"}
          rules={returning ? [{ required: true, whitespace: true, message: "退回要写意见" }] : []}
          extra={returning ? <span style={{ color: token.colorTextSecondary }}>{FINAL_RETURN_HINT}</span> : undefined}
        >
          <Input.TextArea rows={3} maxLength={2000} placeholder="PR 会看到这段文字" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
