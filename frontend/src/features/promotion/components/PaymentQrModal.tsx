// 博主收款码弹窗（流程线 8.4，从「录入信息」拆出来）。
//
// - 打开时拉详情（带 ui.fields 与结款块）：能不能换只看 ui.fields.payment_qr 是不是 edit（7.3 的锁 ① ② ③ ①' 在后端）
// - 只读时按单据情况写一句说明（paymentQrView）；灰显时显示接口的 hint（「发布后上传」）
// - 上传走后端代传；成功后 invalidate ["promotions"]（详情 key 在它下面，一起重拉）与时间线
import { useState } from "react";
import { Alert, Button, Image, Modal, Space, Spin, Typography, Upload, message } from "antd";
import { DeleteOutlined, EyeOutlined, UploadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import {
  getPromotion,
  promotionDetailKey,
  removePaymentQr,
  uploadPaymentQrFile,
} from "@/features/promotion/api";
import { paymentQrView, settlementImageError } from "@/features/promotion/settlementFlow";
import type { Promotion } from "@/features/promotion/types";
import { useFlowFeedback } from "./useFlowFeedback";

/** 收款码的查看 / 上传 / 移除（收款码弹窗与结款重提弹窗共用）。editable = false 只看。 */
export function PaymentQrEditor({ promotion, editable }: { promotion: Promotion; editable: boolean }) {
  const feedback = useFlowFeedback();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const hasQr = Boolean(promotion.payment_qr_attachment_id);

  async function upload() {
    if (!file) return;
    const problem = settlementImageError("收款码", file);
    if (problem) {
      message.error(problem);
      return;
    }
    setBusy(true);
    try {
      await uploadPaymentQrFile(promotion.id, file);
      setFile(null);
      feedback.refresh();
      message.success("博主收款码已上传");
    } catch (err) {
      feedback.handleError(err);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await removePaymentQr(promotion.id);
      feedback.refresh();
      message.success("收款码已移除");
    } catch (err) {
      feedback.handleError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {promotion.payment_qr_signed_url ? (
        <Image src={promotion.payment_qr_signed_url} alt="收款码" width={120} />
      ) : hasQr ? (
        // 签名失败时 URL 为空但图其实在：判断「传过没有」看 attachment_id
        <Typography.Text type="secondary">已上传收款码（暂时无法预览）</Typography.Text>
      ) : (
        <Typography.Text type="secondary">尚未上传收款码</Typography.Text>
      )}
      {editable && (
        <>
          <Upload
            accept="image/jpeg,image/png,image/webp"
            maxCount={1}
            beforeUpload={(f) => {
              setFile(f);
              return false;
            }}
            onRemove={() => setFile(null)}
            fileList={file ? [{ uid: "payment-qr", name: file.name }] : []}
          >
            <Button icon={<UploadOutlined />} disabled={busy}>
              选择图片
            </Button>
          </Upload>
          <Space wrap>
            <Button
              type="primary"
              icon={<UploadOutlined />}
              disabled={!file}
              loading={busy}
              onClick={() => void upload()}
            >
              {hasQr ? "替换收款码" : "上传收款码"}
            </Button>
            {hasQr && (
              <Button
                danger
                icon={<DeleteOutlined />}
                disabled={busy}
                onClick={() =>
                  Modal.confirm({
                    title: "移除收款码？",
                    content: "移除后，财务结款信息中的收款码将不可再查看。",
                    okText: "确认移除",
                    okButtonProps: { danger: true },
                    cancelText: "取消",
                    onOk: remove,
                  })
                }
              >
                移除
              </Button>
            )}
          </Space>
          <Typography.Text type="secondary">
            支持 JPG、PNG、WebP，单张不超过 10MB；文件存储于私有空间。
          </Typography.Text>
        </>
      )}
    </Space>
  );
}

type Props = {
  /** 点的那条推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

export function PaymentQrModal({ target, onClose }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: promotionDetailKey(target?.id ?? ""),
    queryFn: () => getPromotion(target?.id ?? ""),
    enabled: !!target,
  });
  const view = data ? paymentQrView(data) : null;

  return (
    <Modal
      title={target ? `博主收款码 · ${target.internal_code}` : "博主收款码"}
      open={!!target}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      destroyOnHidden
      width={480}
    >
      {isLoading || !data ? (
        <Spin />
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%", marginTop: 8 }}>
          {view?.notice && <Alert type="info" showIcon message={view.notice} />}
          <PaymentQrEditor promotion={data} editable={Boolean(view?.editable)} />
          {data.settlement_payment_proof_signed_url && (
            <Typography.Link
              href={data.settlement_payment_proof_signed_url}
              target="_blank"
              rel="noreferrer"
            >
              <EyeOutlined /> 查看结款凭证（财务上传）
            </Typography.Link>
          )}
        </Space>
      )}
    </Modal>
  );
}
