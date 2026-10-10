import { useEffect } from "react";
import { Button, Form, Input, Modal, Space, Tag, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  recallFailurePromotion,
  recallSuccessPromotion,
  startRecallPromotion,
} from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { recallColor } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要召回的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 召回：发起 / 成功 / 失败。 */
export function RecallModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    if (target) form.resetFields();
  }, [target, form]);

  const recallMutation = useMutation({
    mutationFn: ({
      id,
      step,
      reason,
    }: {
      id: string;
      step: "start" | "success" | "failure";
      reason?: string;
    }) => {
      if (step === "start") {
        return startRecallPromotion(id, { recall_reason: reason ?? null });
      }
      if (step === "success") {
        return recallSuccessPromotion(id);
      }
      return recallFailurePromotion(id);
    },
    onSuccess: () => {
      message.success("召回状态已更新");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={target ? `召回 · ${target.internal_code}` : "召回"}
      open={!!target}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      {target && (
        <div style={{ marginTop: 16 }}>
          <Typography.Paragraph type="secondary">
            当前召回状态：<Tag color={recallColor[target.recall_status]}>
              {target.recall_status}
            </Tag>
            衣服损坏或要寄回都走召回流程，与合作模式无关。
          </Typography.Paragraph>
          {["未召回", "召回失败"].includes(target.recall_status) && (
            <Form
              form={form}
              layout="vertical"
              onFinish={(v: { recall_reason?: string }) =>
                recallMutation.mutate({
                  id: target.id,
                  step: "start",
                  reason: v.recall_reason,
                })
              }
            >
              <Form.Item name="recall_reason" label="召回原因">
                <Input.TextArea rows={2} placeholder="如：衣服有污损，要求寄回（可选）" />
              </Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={recallMutation.isPending}
              >
                发起召回
              </Button>
            </Form>
          )}
          {target.recall_status === "召回中" && (
            <Space>
              <Button
                type="primary"
                loading={recallMutation.isPending}
                onClick={() =>
                  recallMutation.mutate({
                    id: target.id,
                    step: "success",
                  })
                }
              >
                召回成功
              </Button>
              <Button
                danger
                loading={recallMutation.isPending}
                onClick={() =>
                  recallMutation.mutate({
                    id: target.id,
                    step: "failure",
                  })
                }
              >
                召回失败
              </Button>
              <Typography.Text type="secondary">
                失败后还能重新发起
              </Typography.Text>
            </Space>
          )}
          {target.recall_status === "召回成功" && (
            <Typography.Text type="secondary">
              召回已完成，这是终态。寄回运费可以在「录入信息」里补。
            </Typography.Text>
          )}
        </div>
      )}
    </Modal>
  );
}
