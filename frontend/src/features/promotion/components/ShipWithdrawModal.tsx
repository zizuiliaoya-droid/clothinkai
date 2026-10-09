import { useEffect } from "react";
import { Alert, Form, Input, Modal, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { shipWithdraw } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { useFlowFeedback } from "./useFlowFeedback";

type Props = {
  /** 要撤回推送的推广单（`ui.actions.ship_withdraw` 为 enabled 才会打开）；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 撤回推送：待打单 → 待发货，原因必填（1 ~ 500 字，写进操作记录）。 */
export function ShipWithdrawModal({ target, onClose }: Props) {
  const [form] = Form.useForm<{ reason: string }>();
  const { refresh, handleError } = useFlowFeedback();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => shipWithdraw(id, { reason }),
    onSuccess: () => {
      message.success("已撤回推送");
      refresh();
      onClose();
    },
    onError: (err) => handleError(err),
  });

  return (
    <Modal
      title={target ? `撤回推送 · ${target.internal_code}` : "撤回推送"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="确认撤回"
      okButtonProps={{ danger: true }}
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Alert type="warning" showIcon style={{ marginTop: 16 }} message="仓库的待打单里会消失这一单" />
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v) => {
          if (!target) return;
          mutation.mutate({ id: target.id, reason: v.reason.trim() });
        }}
      >
        <Form.Item
          name="reason"
          label="撤回原因"
          rules={[{ required: true, whitespace: true, message: "请填写撤回原因" }]}
        >
          <Input.TextArea rows={3} maxLength={500} showCount placeholder="如：博主换了收件地址，先撤回改好再推" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
