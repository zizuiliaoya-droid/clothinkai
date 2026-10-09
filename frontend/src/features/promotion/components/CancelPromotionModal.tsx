import { useEffect } from "react";
import { Form, Input, Modal, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelPromotion } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要取消的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 取消推广：必须填原因。 */
export function CancelPromotionModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const cancelMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      cancelPromotion(id, { cancel_reason: reason }),
    onSuccess: () => {
      message.success("已取消");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `取消 · ${target.internal_code}` : "取消推广"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={cancelMutation.isPending}
      okButtonProps={{ danger: true }}
      okText="确认取消"
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: { cancel_reason: string }) => {
          if (!target) return;
          cancelMutation.mutate({
            id: target.id,
            reason: v.cancel_reason,
          });
        }}
      >
        <Form.Item
          name="cancel_reason"
          label="取消原因"
          rules={[{ required: true, message: "请填写取消原因" }]}
          tooltip="取消是终态，不能撤回。原因会写入操作记录。"
        >
          <Input.TextArea rows={3} placeholder="如：博主档期冲突，不再合作" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
