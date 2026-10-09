import { useEffect } from "react";
import { Form, Input, Modal, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { submitRetrospective } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要写复盘的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 写复盘（PRD 改动 4）。 */
export function RetroModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (!target) return;
    form.setFieldsValue({ content: target.retro_content ?? "" });
  }, [target, form]);

  const retroMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      submitRetrospective(id, content),
    onSuccess: () => {
      message.success("复盘已提交，等主管确认");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `写复盘 · ${target.internal_code}` : "写复盘"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={retroMutation.isPending}
      destroyOnHidden
      width={560}
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: { content: string }) => {
          if (!target) return;
          retroMutation.mutate({ id: target.id, content: v.content });
        }}
      >
        <Typography.Paragraph type="secondary">
          自由描述：数据表现、博主配合度、是否值得二搭、下次合作建议。
          主管确认后这段文字会永久沉淀到博主档案，下次挑博主时在悬浮卡里直接看到。
        </Typography.Paragraph>
        <Form.Item
          name="content"
          label="复盘内容"
          rules={[{ required: true, message: "请填写复盘内容" }]}
        >
          <Input.TextArea rows={6} maxLength={5000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
