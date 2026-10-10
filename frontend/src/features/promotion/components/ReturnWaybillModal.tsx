import { useEffect } from "react";
import { Form, Input, Modal, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setReturnWaybill } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要填寄回单号的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 博主寄回衣服单号（寄拍的审核门槛）。 */
export function ReturnWaybillModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (!target) return;
    form.setFieldsValue({
      return_waybill: target.return_waybill ?? "",
    });
  }, [target, form]);

  const waybillMutation = useMutation({
    mutationFn: ({ id, waybill }: { id: string; waybill: string }) =>
      setReturnWaybill(id, waybill),
    onSuccess: () => {
      message.success("寄回单号已保存，现在可以提交审核了");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={
        target
          ? `博主寄回衣服单号 · ${target.internal_code}`
          : "博主寄回衣服单号"
      }
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={waybillMutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: { return_waybill: string }) => {
          if (!target) return;
          waybillMutation.mutate({
            id: target.id,
            waybill: v.return_waybill,
          });
        }}
      >
        <Typography.Paragraph type="secondary">
          寄拍模式下，没有这个单号审核通不过，财务也看不到单据。这是博主把衣服寄回来的
          快递单号，不是寄给博主的那个。
        </Typography.Paragraph>
        <Form.Item
          name="return_waybill"
          label="寄回快递单号"
          rules={[{ required: true, message: "请填写寄回单号" }]}
        >
          <Input placeholder="如 SF1234567890" allowClear />
        </Form.Item>
      </Form>
    </Modal>
  );
}
