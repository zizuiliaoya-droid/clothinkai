import { useEffect, useState } from "react";
import { Button, Form, InputNumber, Modal, Space, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { recordMetrics } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要录数据的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 录 7 天数据（复盘第一步，PRD 改动 4）。 */
export function MetricsModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  const [metricsFile, setMetricsFile] = useState<File | null>(null);

  useEffect(() => {
    if (!target) return;
    form.resetFields();
    setMetricsFile(null);
  }, [target, form]);

  const metricsMutation = useMutation({
    mutationFn: (v: {
      id: string;
      like_count: number;
      collect_count: number;
      comment_count: number;
      file: File;
    }) =>
      recordMetrics(
        v.id,
        {
          like_count: v.like_count,
          collect_count: v.collect_count,
          comment_count: v.comment_count,
        },
        v.file
      ),
    onSuccess: () => {
      message.success("数据已录入，进入待复盘");
      onClose();
      setMetricsFile(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={
        target
          ? `录 7 天数据 · ${target.internal_code}`
          : "录 7 天数据"
      }
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={metricsMutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v: {
          like_count: number;
          collect_count: number;
          comment_count: number;
        }) => {
          if (!target) return;
          if (!metricsFile) {
            message.error("请上传数据截图");
            return;
          }
          metricsMutation.mutate({ id: target.id, ...v, file: metricsFile });
        }}
      >
        <Typography.Paragraph type="secondary">
          发布满 7 天后录一次。三个指标和截图都必填，录完进入待复盘。
        </Typography.Paragraph>
        <Space size="large">
          <Form.Item
            name="like_count"
            label="点赞数"
            rules={[{ required: true, message: "必填" }]}
          >
            <InputNumber min={0} style={{ width: 130 }} />
          </Form.Item>
          <Form.Item
            name="collect_count"
            label="收藏数"
            rules={[{ required: true, message: "必填" }]}
          >
            <InputNumber min={0} style={{ width: 130 }} />
          </Form.Item>
          <Form.Item
            name="comment_count"
            label="评论数"
            rules={[{ required: true, message: "必填" }]}
          >
            <InputNumber min={0} style={{ width: 130 }} />
          </Form.Item>
        </Space>
        <Form.Item label="数据截图" required>
          <Upload
            accept="image/png,image/jpeg,image/webp"
            maxCount={1}
            beforeUpload={(file) => {
              setMetricsFile(file as unknown as File);
              return false;
            }}
            onRemove={() => setMetricsFile(null)}
          >
            <Button icon={<UploadOutlined />}>选择截图</Button>
          </Upload>
        </Form.Item>
      </Form>
    </Modal>
  );
}
