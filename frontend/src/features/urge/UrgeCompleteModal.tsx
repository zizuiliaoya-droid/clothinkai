import { useState } from "react";
import { Button, Form, Input, Modal, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  URGE_SCREENSHOT_MIME_TYPES,
  completeUrgeTask,
  screenshotProblem,
} from "@/features/urge/api";
import type { UrgeTask, UrgeTaskDetail } from "@/features/urge/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要标记完成的任务；null 表示弹窗关着。 */
  task: UrgeTask | null;
  onClose: () => void;
  onCompleted?: (detail: UrgeTaskDetail) => void;
};

/**
 * 催发「标记完成」弹窗（流程线 3.9 U1、8.7）。
 *
 * 截图必传（Q8）：没选图「标记完成」按钮是灰的，后端也会 422 兜底。
 * 完成后任务关闭、原因记「已完成」；之后再手动催会重新打开任务。
 */
export function UrgeCompleteModal({ task, onClose, onCompleted }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm<{ note?: string }>();
  const [screenshot, setScreenshot] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: ({ id, file, note }: { id: string; file: File; note?: string }) =>
      completeUrgeTask(id, file, note),
    onSuccess: (d) => {
      message.success("已标记完成");
      void qc.invalidateQueries({ queryKey: ["urge-tasks"] });
      void qc.invalidateQueries({ queryKey: ["urge-dashboard"] });
      void qc.invalidateQueries({ queryKey: ["urge-task", d.id] });
      onCompleted?.(d);
      close();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function close() {
    form.resetFields();
    setScreenshot(null);
    onClose();
  }

  return (
    <Modal
      title={
        task
          ? `标记完成 · ${task.promotion_internal_code ?? "—"} · ${task.blogger_nickname ?? "—"}`
          : "标记完成"
      }
      open={!!task}
      onCancel={close}
      onOk={() => form.submit()}
      okText="标记完成"
      okButtonProps={{ disabled: !screenshot }}
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v) => {
          if (!task || !screenshot) return;
          mutation.mutate({
            id: task.id,
            file: screenshot,
            // 全空白等于没写（multipart 后端不去空白）
            note: v.note?.trim() || undefined,
          });
        }}
      >
        <Typography.Paragraph type="secondary">
          博主已发布或已确认时用。标记后任务关闭，之后再催发会重新打开任务。
        </Typography.Paragraph>
        <Form.Item
          label="截图"
          required
          extra="必传：博主已发布或已确认的截图。支持 JPG、PNG、WebP，不超过 10MB"
        >
          <Upload
            accept={URGE_SCREENSHOT_MIME_TYPES.join(",")}
            maxCount={1}
            beforeUpload={(file) => {
              const problem = screenshotProblem(file);
              if (problem) {
                message.error(problem);
                return Upload.LIST_IGNORE;
              }
              setScreenshot(file);
              return false;
            }}
            onRemove={() => setScreenshot(null)}
          >
            <Button icon={<UploadOutlined />}>选择截图</Button>
          </Upload>
        </Form.Item>
        <Form.Item name="note" label="备注">
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            placeholder="补充说明（可选，会写进时间线）"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
