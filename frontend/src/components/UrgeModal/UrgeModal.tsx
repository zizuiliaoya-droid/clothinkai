import { useState } from "react";
import { Button, Form, Input, Modal, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  URGE_SCREENSHOT_MAX_BYTES,
  URGE_SCREENSHOT_MIME_TYPES,
  urgePromotion,
  urgePromotionWithScreenshot,
} from "@/features/urge/api";
import type { UrgeTaskDetail } from "@/features/urge/types";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  open: boolean;
  /** 要催的推广单；open 为 true 时必须有值。 */
  promotionId: string | null;
  title: string;
  onClose: () => void;
  onUrged?: (detail: UrgeTaskDetail) => void;
};

const ALLOWED_TYPES: readonly string[] = URGE_SCREENSHOT_MIME_TYPES;

/** 和后端 check_image_payload 同样的顺序与措辞（后端还会核对文件头，这里只挡格式与大小）。 */
function screenshotProblem(file: File): string | null {
  if (!ALLOWED_TYPES.includes(file.type)) return "催发截图仅支持 JPG、PNG、WebP 格式";
  if (file.size === 0) return "催发截图文件不能为空";
  if (file.size > URGE_SCREENSHOT_MAX_BYTES) return "催发截图文件不能超过 10MB";
  return null;
}

/**
 * 手动催发弹窗（7a-3）：催发任务页与推广页共用。
 *
 * 备注、截图都可选。有截图走 urge-with-screenshot（multipart，后端代传 R2），没有走 urge。
 * 前端先按后端同一套规则挡格式与大小，省得传完 10MB 才被拒。
 */
export function UrgeModal({ open, promotionId, title, onClose, onUrged }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm<{ note?: string }>();
  const [screenshot, setScreenshot] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: ({ id, note, file }: { id: string; note?: string; file: File | null }) =>
      file ? urgePromotionWithScreenshot(id, file, note) : urgePromotion(id, note),
    onSuccess: (d) => {
      message.success(`已催发，这是第 ${d.urge_count} 次`);
      void qc.invalidateQueries({ queryKey: ["urge-tasks"] });
      void qc.invalidateQueries({ queryKey: ["urge-dashboard"] });
      void qc.invalidateQueries({ queryKey: ["urge-task", d.id] });
      onUrged?.(d);
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
      title={title}
      open={open}
      onCancel={close}
      onOk={() => form.submit()}
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(v) => {
          if (!promotionId) return;
          mutation.mutate({
            id: promotionId,
            // 全空白等于没写；JSON 那条路后端会去空白，multipart 不会，这里统一
            note: v.note?.trim() || undefined,
            file: screenshot,
          });
        }}
      >
        <Typography.Paragraph type="secondary">
          记一次催发。第一次催会自动建催发任务，之后累加次数。截图可选。
        </Typography.Paragraph>
        <Form.Item name="note" label="备注">
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            placeholder="怎么催的、博主怎么回的（可选，会写进催发时间线）"
          />
        </Form.Item>
        <Form.Item label="聊天截图" extra="可选。支持 JPG、PNG、WebP，不超过 10MB">
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
      </Form>
    </Modal>
  );
}
