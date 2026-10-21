import { Alert, Form, Input, InputNumber, Modal, Radio, Space, Spin, Typography, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { Promotion } from "../types";
import type { ReturnFreightPayment } from "../freightTypes";
import { getPromotion, promotionDetailKey } from "../api";
import { freightDetailKey, getFreightPayment, resubmitFreightPayment, reviewFreightPayment, submitFreightPayment } from "../freightApi";
import { paymentQrView } from "../settlementFlow";
import { FreightSnapshot } from "./FreightSnapshot";
import { PaymentQrEditor } from "./PaymentQrModal";
import { RecallWaybillEditor } from "./RecallWaybillModal";
import { useRecallFreightFeedback } from "./useRecallFreightFeedback";
import { FreightPaymentModal } from "@/features/finance/components/FreightPaymentModal";

export type FreightModalMode = "submit" | "review" | "resubmit" | "view" | "pay" | "reject";
type Props = { target: Promotion | null; mode: FreightModalMode; onClose: () => void };
type Values = { amount: string | null; note?: string; action: "approve" | "reject"; reason?: string };
export function FreightModal({ target, mode, onClose }: Props) {
  const detail = useQuery({ queryKey: promotionDetailKey(target?.id ?? ""), queryFn: () => getPromotion(target!.id), enabled: !!target });
  const id = detail.data?.freight_payment?.id;
  const freight = useQuery({ queryKey: freightDetailKey(id ?? ""), queryFn: () => getFreightPayment(id!), enabled: !!target && !!id });
  if (!target) return null;
  if (detail.error || freight.error) return <Modal open title="寄回运费" onCancel={onClose} footer={null}><Alert type="error" message="寄回运费加载失败，请关闭后重试" /></Modal>;
  if (!detail.data || id && !freight.data) return <Modal open title="寄回运费" onCancel={onClose} footer={null}><Spin /></Modal>;
  if ((mode === "pay" || mode === "reject") && freight.data) return <FreightPaymentModal key={`${id}-${mode}`} target={freight.data} mode={mode} onClose={onClose} />;
  return <FreightForm key={`${target.id}-${mode}`} promotion={detail.data} freight={freight.data} mode={mode} onClose={onClose} />;
}
function FreightForm({ promotion, freight, mode, onClose }: { promotion: Promotion; freight?: ReturnFreightPayment; mode: FreightModalMode; onClose: () => void }) {
  const [form] = Form.useForm<Values>();
  const [qrPending, setQrPending] = useState(false);
  const [waybillPending, setWaybillPending] = useState(false);
  const actionValue = Form.useWatch("action", form) ?? "approve";
  const action = mode === "submit" ? promotion.ui?.actions?.freight_submit : freight?.ui.actions?.[mode === "review" ? "freight_review" : "freight_resubmit"];
  const enabled = mode !== "view" && action?.state === "enabled";
  const feedback = useRecallFreightFeedback(promotion.line_id);
  const mutation = useMutation({
    mutationFn: (value: Values) => {
      if (!enabled) throw new Error("当前付款单只读");
      if (mode === "review" && freight) return reviewFreightPayment(freight.id, { action: value.action, ...(value.action === "reject" ? { reason: value.reason!.trim() } : {}) });
      if (mode === "resubmit" && freight) return resubmitFreightPayment(freight.id, { amount: String(value.amount), note: value.note!.trim() });
      return submitFreightPayment(promotion.id, String(value.amount));
    },
    onSuccess: () => { message.success(mode === "review" ? "运费审核已提交" : "寄回运费已提交审核"); feedback.refresh(); onClose(); },
    onError: feedback.handleError,
  });
  const editing = mode === "submit" || mode === "resubmit";
  return <Modal open title={`寄回运费 · ${promotion.internal_code}`} onCancel={onClose} onOk={() => form.submit()} okText={mode === "review" ? "确认审核" : mode === "resubmit" ? "重提寄回运费" : "提交寄回运费"} okButtonProps={{ disabled: !enabled || editing && (qrPending || waybillPending) }} footer={mode === "view" ? null : undefined} confirmLoading={mutation.isPending} destroyOnHidden width={600}>
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      {freight && <FreightSnapshot data={freight} />}
      {!enabled && mode !== "view" && <Alert type="info" message={action?.reason ?? action?.hint ?? "当前付款单只读"} />}
      <Form form={form} layout="vertical" style={{ width: "100%" }} initialValues={{ amount: freight ? freight.amount : promotion.return_shipping_fee ?? null, action: "approve" }} onFinish={(value) => mutation.mutate(value)} disabled={!enabled || mutation.isPending}>
        {editing && <>
          <Form.Item name="amount" label="运费金额" rules={[{ required: true, message: "请填写运费金额" }]}><InputNumber stringMode min="0.01" max="99999999.99" precision={2} prefix="¥" style={{ width: "100%" }} /></Form.Item>
          <Typography.Paragraph type="secondary">承担方为博主垫付。提交时保存收款码与寄回截图快照。</Typography.Paragraph>
          {mode === "resubmit" && <Form.Item name="note" label="重提说明" rules={[{ required: true, whitespace: true, message: "请填写重提说明" }, { max: 2000, message: "最多 2000 字" }]}><Input.TextArea rows={3} maxLength={2000} showCount /></Form.Item>}
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            {promotion.ui?.fields?.payment_qr && <><Typography.Text strong>当前博主收款码</Typography.Text><PaymentQrEditor promotion={promotion} editable={paymentQrView(promotion).editable} allowRemove={false} onPendingChange={setQrPending} /></>}
            <Typography.Text strong>当前寄回截图</Typography.Text><RecallWaybillEditor promotion={promotion} onPendingChange={setWaybillPending} />
          </Space>
        </>}
        {mode === "review" && <>
          <Form.Item name="action" label="审核结果"><Radio.Group options={[{ label: "通过", value: "approve" }, { label: "驳回", value: "reject" }]} /></Form.Item>
          {actionValue === "reject" && <Form.Item name="reason" label="驳回原因" rules={[{ required: true, whitespace: true, message: "请填写驳回原因" }, { max: 2000, message: "最多 2000 字" }]}><Input.TextArea rows={3} maxLength={2000} showCount /></Form.Item>}
        </>}
      </Form>
    </Space>
  </Modal>;
}
