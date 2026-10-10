import { useEffect } from "react";
import { Form, Input, Modal, Select, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { reviewPromotion } from "@/features/promotion/api";
import type { Promotion, RejectReasonCategory } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

/** 驳回原因分类，三选一必填（PRD 改动 5）。 */
const REJECT_CATEGORIES: RejectReasonCategory[] = [
  "延迟发文",
  "流量差补发",
  "衣服未寄回",
];

type Props = {
  /** 要驳回的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 审核驳回。 */
export function RejectModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  /** 驳回必须带原因分类 + 文字说明，后端两者都校验。 */
  const rejectMutation = useMutation({
    mutationFn: ({
      id,
      category,
      reason,
    }: {
      id: string;
      category: RejectReasonCategory;
      reason: string;
    }) =>
      reviewPromotion(id, {
        action: "reject",
        review_reason: reason,
        review_reason_category: category,
      }),
    onSuccess: () => {
      message.success("已驳回");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `驳回 · ${target.internal_code}` : "驳回"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={rejectMutation.isPending}
      okButtonProps={{ danger: true }}
      okText="确认驳回"
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: {
          review_reason_category: RejectReasonCategory;
          review_reason: string;
        }) => {
          if (!target) return;
          rejectMutation.mutate({
            id: target.id,
            category: v.review_reason_category,
            reason: v.review_reason,
          });
        }}
      >
        <Form.Item
          name="review_reason_category"
          label="驳回原因分类"
          rules={[{ required: true, message: "请选择驳回原因分类" }]}
          tooltip="分类用于统计哪类问题最多，也决定后续动作（衣服未寄回要催寄回，流量差补发要重新排期）。"
        >
          <Select
            placeholder="三选一"
            options={REJECT_CATEGORIES.map((c) => ({ label: c, value: c }))}
          />
        </Form.Item>
        <Form.Item
          name="review_reason"
          label="说明"
          rules={[{ required: true, message: "请填写驳回说明" }]}
        >
          <Input.TextArea rows={3} placeholder="具体说明，PR 会看到这段文字" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
