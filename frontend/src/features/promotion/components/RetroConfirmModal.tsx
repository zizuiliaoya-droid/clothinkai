import { useEffect } from "react";
import { Button, Form, Input, Modal, Space, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { confirmRetrospective } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要确认复盘的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 主管确认 / 打回复盘（PRD 改动 4）。 */
export function RetroConfirmModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const retroConfirmMutation = useMutation({
    mutationFn: ({
      id,
      approve,
      opinion,
    }: {
      id: string;
      approve: boolean;
      opinion?: string;
    }) => confirmRetrospective(id, approve, opinion),
    onSuccess: (_d, v) => {
      message.success(v.approve ? "复盘已确认，单据完结" : "已打回，PR 需重写");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      void qc.invalidateQueries({ queryKey: ["blogger-retrospectives"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={
        target
          ? `确认复盘 · ${target.internal_code}`
          : "确认复盘"
      }
      open={!!target}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
      width={560}
    >
      {target && (
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v: { opinion?: string }) => {
            retroConfirmMutation.mutate({
              id: target.id,
              approve: true,
              opinion: v.opinion,
            });
          }}
        >
          <Typography.Paragraph type="secondary">
            PR 写的复盘：
          </Typography.Paragraph>
          <Typography.Paragraph
            style={{
              whiteSpace: "pre-wrap",
              background: "#fafafa",
              border: "1px solid #f0f0f0",
              borderRadius: 4,
              padding: 12,
            }}
          >
            {target.retro_content || "（没有内容）"}
          </Typography.Paragraph>
          <Form.Item
            name="opinion"
            label="意见"
            extra="确认时可留空；打回时必填，PR 得知道要改什么"
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              loading={retroConfirmMutation.isPending}
              onClick={() => form.submit()}
            >
              确认通过
            </Button>
            <Button
              danger
              loading={retroConfirmMutation.isPending}
              onClick={() => {
                const opinion = form.getFieldValue("opinion");
                if (!opinion) {
                  message.error("打回时必须写明意见");
                  return;
                }
                retroConfirmMutation.mutate({
                  id: target.id,
                  approve: false,
                  opinion,
                });
              }}
            >
              打回重写
            </Button>
          </Space>
        </Form>
      )}
    </Modal>
  );
}
