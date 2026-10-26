import { Alert, Form, Input, InputNumber, Modal, Radio, Space, Spin, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getPromotion, promotionDetailKey, recallFailurePromotion, recallSuccessPromotion } from "../api";
import type { Promotion } from "../types";
import { paymentQrView } from "../settlementFlow";
import { PaymentQrEditor } from "./PaymentQrModal";
import { RecallWaybillEditor } from "./RecallWaybillModal";
import { useRecallFreightFeedback } from "./useRecallFreightFeedback";

type Props = { target: Promotion | null; onClose: () => void };
type Values = { result: "success" | "failure"; amount: string | null; payer: "博主垫付" | "到付"; remark?: string };
export function RecallResultModal({ target, onClose }: Props) {
  const { data, isLoading, error } = useQuery({ queryKey: promotionDetailKey(target?.id ?? ""), queryFn: () => getPromotion(target!.id), enabled: !!target });
  if (!target) return null;
  if (isLoading || !data || error) return <Modal open title="登记召回结果" onCancel={onClose} footer={null}>{error ? <Alert type="error" message="单据详情加载失败" /> : <Spin />}</Modal>;
  return <ResultForm key={target.id} promotion={data} onClose={onClose} />;
}
function ResultForm({ promotion, onClose }: { promotion: Promotion; onClose: () => void }) {
  const [form] = Form.useForm<Values>();
  const [qrPending, setQrPending] = useState(false);
  const [waybillPending, setWaybillPending] = useState(false);
  const result = Form.useWatch("result", form) ?? "success";
  const amount = Form.useWatch("amount", form);
  const payer = Form.useWatch("payer", form) ?? "博主垫付";
  const needsQr = result === "success" && payer === "博主垫付" && Number(amount) > 0;
  const enabled = promotion.ui?.actions?.recall_result?.state === "enabled";
  const feedback = useRecallFreightFeedback(promotion.line_id);
  const mutation = useMutation({
    mutationFn: async (value: Values) => {
      if (!enabled) throw new Error("当前单据不可登记召回结果");
      if (value.result === "failure") return recallFailurePromotion(promotion.id, { remark: value.remark?.trim() || undefined });
      if (!promotion.recall_waybill_attachment_id) throw new Error("请先上传寄回截图");
      if (value.payer === "博主垫付" && Number(value.amount) > 0 && !promotion.payment_qr_attachment_id) throw new Error("请先上传博主收款码");
      return recallSuccessPromotion(promotion.id, { return_shipping_fee: String(value.amount), return_shipping_payer: promotion.freight_payment ? "博主垫付" : value.payer, remark: value.remark?.trim() || undefined });
    },
    onSuccess: () => { message.success("召回结果已登记"); feedback.refresh(); onClose(); },
    onError: feedback.handleError,
  });
  return <Modal open title={`登记召回结果 · ${promotion.internal_code}`} onCancel={onClose} onOk={() => form.submit()} okText="登记召回结果" okButtonProps={{ disabled: !enabled || result === "success" && (waybillPending || needsQr && qrPending) }} confirmLoading={mutation.isPending} destroyOnHidden width={560}>
    {!enabled && <Alert type="info" message={promotion.ui?.actions?.recall_result?.reason ?? "当前单据只读"} />}
    <Form form={form} layout="vertical" initialValues={{ result: "success", payer: promotion.return_shipping_payer ?? "博主垫付", amount: promotion.return_shipping_fee ?? null }} onFinish={(value) => mutation.mutate(value)} disabled={!enabled || mutation.isPending}>
      <Form.Item name="result" label="召回结果"><Radio.Group options={[{ label: "召回成功", value: "success" }, { label: "召回失败", value: "failure" }]} /></Form.Item>
      {result === "success" && <>
        <Form.Item name="amount" label="寄回运费" rules={[{ required: true, message: "请填写运费，免费请填 0" }]}><InputNumber stringMode readOnly={!!promotion.freight_payment} min="0" max="99999999.99" precision={2} prefix="¥" style={{ width: "100%" }} /></Form.Item>
        {promotion.freight_payment ? <Alert type="info" message="已有寄回运费付款单，承担方固定为博主垫付；金额只能在运费单驳回后重提修改" /> : <Form.Item name="payer" label="承担方" rules={[{ required: true }]}><Radio.Group options={["博主垫付", "到付"]} /></Form.Item>}
        <Space direction="vertical" size={12} style={{ width: "100%", marginBottom: 16 }}>
          <RecallWaybillEditor promotion={promotion} onPendingChange={setWaybillPending} />
          {needsQr && <><Alert type="info" showIcon message="会生成寄回运费付款单，需要博主收款码" /><PaymentQrEditor promotion={promotion} editable={paymentQrView(promotion).editable} allowRemove={false} onPendingChange={setQrPending} /></>}
        </Space>
      </>}
      <Form.Item name="remark" label="结果备注" rules={[{ max: 2000, message: "最多 2000 字" }]}><Input.TextArea rows={3} maxLength={2000} showCount /></Form.Item>
    </Form>
  </Modal>;
}
