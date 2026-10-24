import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { useEffect, useState } from "react";
import { Alert, Button, Modal, Space, Spin, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { getPromotion, promotionDetailKey, uploadRecallWaybillFile } from "../api";
import type { Promotion } from "../types";
import { settlementImageError } from "../settlementFlow";
import { useRecallFreightFeedback } from "./useRecallFreightFeedback";

export function RecallWaybillEditor({ promotion, onPendingChange }: { promotion: Promotion; onPendingChange?: (pending: boolean) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const feedback = useRecallFreightFeedback(promotion.line_id);
  const action = promotion.ui?.actions?.recall_waybill;
  const editable = action?.state === "enabled";
  useEffect(() => { onPendingChange?.(Boolean(file) || busy); }, [file, busy, onPendingChange]);
  async function upload() {
    if (!file || !editable) return;
    setBusy(true);
    try { await uploadRecallWaybillFile(promotion.id, file); setFile(null); feedback.refresh(); message.success("寄回截图已上传，旧图保留在时间线"); }
    catch (error) { feedback.handleError(error); }
    finally { setBusy(false); }
  }
  return <Space direction="vertical" style={{ width: "100%" }}>
    {promotion.recall_waybill_signed_url ? <StyleImageThumbnail size={120} src={promotion.recall_waybill_signed_url} alt="召回寄回截图" /> : promotion.recall_waybill_attachment_id ? <Typography.Text>已上传寄回截图（暂时无法预览）</Typography.Text> : <Typography.Text type="secondary">尚未上传寄回截图</Typography.Text>}
    {editable ? <>
      <Alert type="info" message="只截单号与运费部分" />
      <Upload accept="image/jpeg,image/png,image/webp" disabled={busy} maxCount={1} fileList={file ? [{ uid: "recall-waybill", name: file.name }] : []} onRemove={() => setFile(null)} beforeUpload={(next) => { const problem = settlementImageError("寄回截图", next); if (problem) { message.error(problem); return Upload.LIST_IGNORE; } setFile(next); return false; }}>
        <Button icon={<UploadOutlined />} disabled={busy}>选择寄回截图</Button>
      </Upload>
      <Button type="primary" onClick={() => void upload()} disabled={!file} loading={busy}>上传寄回截图</Button>
      <Typography.Text type="secondary">JPG、PNG、WebP，单张不超过 10MB</Typography.Text>
    </> : <Typography.Text type="secondary">{action?.reason ?? action?.hint ?? "当前寄回截图只读"}</Typography.Text>}
  </Space>;
}
export function RecallWaybillModal({ target, onClose }: { target: Promotion | null; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: promotionDetailKey(target?.id ?? ""), queryFn: () => getPromotion(target!.id), enabled: !!target });
  return <Modal title={`召回寄回截图${target ? ` · ${target.internal_code}` : ""}`} open={!!target} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>} destroyOnHidden>
    {isLoading ? <Spin /> : error || !data ? <Alert type="error" message="单据详情加载失败" /> : <RecallWaybillEditor key={data.id} promotion={data} />}
  </Modal>;
}
