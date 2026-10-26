import { useState } from "react";
import { Alert, Button, Form, Input, Modal, Space, Spin, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { freightDetailKey, getFreightPayment, payFreightPayment, rejectFreightPayment } from "@/features/promotion/freightApi";
import type { ReturnFreightPayment } from "@/features/promotion/freightTypes";
import { settlementImageError } from "@/features/promotion/settlementFlow";
import { FreightSnapshot } from "@/features/promotion/components/FreightSnapshot";
import { useRecallFreightFeedback } from "@/features/promotion/components/useRecallFreightFeedback";

export function FreightPaymentModal({ target, mode, onClose }: { target: ReturnFreightPayment | null; mode: "pay" | "reject"; onClose: () => void }) {
  const id = target?.id ?? "";
  const [file, setFile] = useState<File | null>(null);
  const [form] = Form.useForm<{ reason: string }>();
  const { data, isLoading, error } = useQuery({ queryKey: freightDetailKey(id), queryFn: () => getFreightPayment(id), enabled: !!id });
  const feedback = useRecallFreightFeedback(target?.line_id);
  const key = mode === "pay" ? "freight_pay" : "freight_reject_payment";
  const capability = data?.ui.actions?.[key];
  const canSubmit = capability?.state === "enabled";
  const mutation = useMutation({
    mutationFn: async (reason?: string) => {
      if (!canSubmit) throw new Error("当前操作不可用，请刷新后重试");
      if (mode === "pay") {
        if (!file) throw new Error("请先选择付款凭证");
        return payFreightPayment(id, file);
      }
      return rejectFreightPayment(id, reason!.trim());
    },
    onSuccess: () => { message.success(mode === "pay" ? "寄回运费已付款" : "寄回运费已驳回"); feedback.refresh(); onClose(); },
    onError: feedback.handleError,
  });
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date());
  return <Modal title={`${mode === "pay" ? "支付" : "驳回"}寄回运费${target ? ` · ${target.promotion_internal_code}` : ""}`} open={!!target} onCancel={onClose} okText={mode === "pay" ? "确认付款" : "确认驳回"} onOk={() => mode === "pay" ? mutation.mutate(undefined) : form.submit()} okButtonProps={{ disabled: !canSubmit || mode === "pay" && !file }} confirmLoading={mutation.isPending} destroyOnHidden width={560}>
    {isLoading ? <Spin /> : error || !data ? <Alert type="error" message="付款单加载失败，请关闭后重试" /> : <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <FreightSnapshot data={data} />
      {!canSubmit && <Alert type="info" message={capability?.reason ?? capability?.hint ?? "当前付款单只读"} />}
      {mode === "pay" ? <Form layout="vertical">
        <Typography.Paragraph>付款日期：今天（{today}），系统自动记录（北京时间）</Typography.Paragraph>
        <Form.Item label="付款凭证" required>
          <Upload accept="image/jpeg,image/png,image/webp" maxCount={1} disabled={!canSubmit || mutation.isPending} fileList={file ? [{ uid: "freight-proof", name: file.name }] : []} onRemove={() => setFile(null)} beforeUpload={(next) => { const problem = settlementImageError("付款凭证", next); if (problem) { message.error(problem); return Upload.LIST_IGNORE; } setFile(next); return false; }}>
            <Button icon={<UploadOutlined />} disabled={!canSubmit || mutation.isPending}>选择付款凭证</Button>
          </Upload>
          <Typography.Text type="secondary">JPG、PNG、WebP，单张不超过 10MB</Typography.Text>
        </Form.Item>
      </Form> : <Form form={form} layout="vertical" onFinish={(value) => mutation.mutate(value.reason)}>
        <Form.Item name="reason" label="驳回原因" rules={[{ required: true, whitespace: true, message: "请填写驳回原因" }, { max: 2000, message: "最多 2000 字" }]}>
          <Input.TextArea rows={3} maxLength={2000} showCount disabled={!canSubmit || mutation.isPending} />
        </Form.Item>
      </Form>}
    </Space>}
  </Modal>;
}
