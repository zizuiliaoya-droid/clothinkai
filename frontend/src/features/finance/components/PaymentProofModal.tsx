// 结款单「上传付款凭证」弹窗（流程线 8.6、F4）。
// 没有付款日期选择：付款日期一律记上传当天（服务端取北京时间），弹窗里写一行说明。
// 选文件时就挡掉格式、大小不对的（与后端 check_image_payload 同一套限制），并显示文件名与大小（8.9）。
import { useEffect, useState } from "react";
import { Button, Form, Modal, Upload, message, theme } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { PAYMENT_PROOF_MAX_BYTES, PAYMENT_PROOF_MIME_TYPES, uploadPaymentProof } from "../api";
import type { Settlement } from "../types";

export interface PaymentProofModalProps {
  target: Settlement | null;
  onClose: () => void;
  onChanged: () => void;
}

function sizeLabel(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function PaymentProofModal({ target, onClose, onChanged }: PaymentProofModalProps) {
  const { token } = theme.useToken();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (target) setFile(null);
  }, [target]);

  async function submit() {
    if (!target) return;
    if (!file) {
      message.error("请先选择付款截图");
      return;
    }
    setUploading(true);
    try {
      await uploadPaymentProof(target.id, file);
      message.success("付款凭证已上传，转「已付款」");
      onChanged();
      onClose();
    } catch (err) {
      const outcome = flowErrorOutcome(err);
      if (outcome.kind === "gate") {
        message.error(`缺：${outcome.missing.map((m) => m.label).join("、")}`);
        return;
      }
      message.error(outcome.text);
      if (outcome.refresh) {
        onChanged();
        onClose();
      }
    } finally {
      setUploading(false);
    }
  }

  return (
    <Modal
      title={target ? `上传付款凭证 · ${target.settlement_no}` : "上传付款凭证"}
      open={!!target}
      onCancel={onClose}
      onOk={() => void submit()}
      confirmLoading={uploading}
      destroyOnHidden
    >
      <Form layout="vertical">
        <Form.Item>
          <span>付款日期：今天（{dayjs().format("YYYY-MM-DD")}），系统自动记录</span>
        </Form.Item>
        <Form.Item label="付款截图" required>
          <Upload
            beforeUpload={(f) => {
              // 先在本地挡掉格式和大小不对的文件，省得传完 10MB 才被后端拒
              if (!(PAYMENT_PROOF_MIME_TYPES as readonly string[]).includes(f.type)) {
                message.error("付款截图仅支持 JPG、PNG、WebP 图片");
                return Upload.LIST_IGNORE;
              }
              if (f.size > PAYMENT_PROOF_MAX_BYTES) {
                message.error("付款截图不能超过 10MB");
                return Upload.LIST_IGNORE;
              }
              setFile(f as File);
              return false;
            }}
            maxCount={1}
            showUploadList={false}
            accept={PAYMENT_PROOF_MIME_TYPES.join(",")}
          >
            <Button icon={<UploadOutlined />}>选择图片</Button>
          </Upload>
          <div style={{ color: token.colorTextSecondary, fontSize: token.fontSizeSM, marginTop: 4 }}>
            {file ? `已选：${file.name}（${sizeLabel(file.size)}）` : "支持 JPG、PNG、WebP，不超过 10MB"}
          </div>
        </Form.Item>
      </Form>
    </Modal>
  );
}
