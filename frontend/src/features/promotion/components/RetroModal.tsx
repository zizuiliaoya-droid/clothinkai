import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { useEffect } from "react";
import { Form, Input, Modal, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { writeRetrospective } from "@/features/promotion/api";
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
      writeRetrospective(id, content),
    onSuccess: () => {
      message.success(target?.retro_status === "已完成" ? "复盘已更新，旧版已留档" : "复盘已完成，已写入博主档案");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      void qc.invalidateQueries({ queryKey: ["blogger-retrospectives"] });
      void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `${target.retro_status === "已完成" ? "修改复盘" : "写复盘"} · ${target.internal_code}` : "写复盘"}
      okText={target?.retro_status === "已完成" ? "保存新版本" : "完成复盘"}
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
          主管写完即进博主档案；管理员修改会保留旧版，档案展示最新有效版。
        </Typography.Paragraph>
        <Form.Item
          name="content"
          label="复盘内容"
          rules={[{ required: true, whitespace: true, message: "请填写复盘内容" }]}
        >
          <Input.TextArea rows={6} maxLength={5000} showCount />
        </Form.Item>
      </Form>
    </Modal>
  );
}
