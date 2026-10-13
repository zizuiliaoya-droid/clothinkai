// 结款单「填付款金额」弹窗（流程线 8.6、F3）。
// 实付默认 = 应付，实时显示「与应付相差 ¥200.00」；不相等时「调整说明」必填（例：延迟发文、限流，原价 400、实付 200）。
// 应付看不到时前端不判，后端 422 FLOW_GATE_MISSING（key = payment_adjust_note）照样标回调整说明。
// 409 SETTLEMENT_TOTAL_CHANGED（填写期间应付被改）只提示后端文案，不按「别人处理过」关窗刷新。
import { useEffect } from "react";
import { Form, Input, InputNumber, Modal, message, theme } from "antd";
import { useMutation } from "@tanstack/react-query";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { missingToFields } from "@/features/flow/missingToFields";
import { extractErrorMessage } from "@/services/apiClient";
import { fillPaymentAmount } from "../api";
import { paymentDiffCents, paymentDiffText } from "../paymentAdjust";
import type { Settlement } from "../types";

interface Values {
  payment_amount?: string | null;
  payment_adjust_note?: string | null;
}

const FIELD_MAP = { payment_adjust_note: "payment_adjust_note", payment_amount: "payment_amount" };
const ADJUST_NOTE_REQUIRED_TEXT = "实付与应付不一致，请写调整说明";
const TOTAL_CHANGED_CODE = "SETTLEMENT_TOTAL_CHANGED";

interface ErrorShape {
  response?: { data?: { code?: unknown } };
}

export interface PaymentAmountModalProps {
  target: Settlement | null;
  onClose: () => void;
  /** 成功或 409 之后刷新列表（与抽屉时间线）。 */
  onChanged: () => void;
}

export function PaymentAmountModal({ target, onClose, onChanged }: PaymentAmountModalProps) {
  const { token } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const paid = Form.useWatch("payment_amount", form);
  const diff = paymentDiffCents(target?.total_amount, paid);
  const needNote = diff != null && diff !== 0;

  useEffect(() => {
    if (target) form.setFieldsValue({ payment_amount: target.total_amount ?? null, payment_adjust_note: null });
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: (v: Values) => {
      if (!target) throw new Error("没有选中的结款单");
      const note = v.payment_adjust_note?.trim();
      return fillPaymentAmount(target.id, {
        payment_amount: Number(v.payment_amount).toFixed(2),
        payment_adjust_note: note ? note : null,
      });
    },
    onSuccess: () => {
      message.success("付款金额已填写，转「待财务付款」");
      onChanged();
      onClose();
    },
    onError: (err) => {
      // 409 应付刚被改（同时有人增加结算项）：照普通错误提示后端文案，弹窗不关、不自动刷新
      if ((err as ErrorShape | null | undefined)?.response?.data?.code === TOTAL_CHANGED_CODE) {
        message.error(extractErrorMessage(err));
        return;
      }
      const outcome = flowErrorOutcome(err);
      if (outcome.kind === "gate") {
        const { fields, rest } = missingToFields(outcome.missing, FIELD_MAP);
        // missingToFields 的字段名是宽类型，按 setFields 的入参收（同 NegotiationReceiverModal）
        form.setFields(fields as Parameters<typeof form.setFields>[0]);
        if (rest) message.error(rest);
        return;
      }
      message.error(outcome.text);
      if (outcome.refresh) {
        onChanged();
        onClose();
      }
    },
  });

  return (
    <Modal
      title={target ? `填写付款金额 · ${target.settlement_no}` : "填写付款金额"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Form<Values> form={form} layout="vertical" onFinish={(v) => mutation.mutate(v)}>
        <Form.Item label="应付金额">
          <span>{target?.total_amount != null ? `¥${target.total_amount}` : "—"}</span>
        </Form.Item>
        <Form.Item
          name="payment_amount"
          label="实付金额"
          rules={[{ required: true, message: "请输入实付金额" }]}
          extra={
            diff != null ? (
              <span aria-live="polite" style={{ color: needNote ? token.colorText : token.colorTextSecondary }}>
                {paymentDiffText(diff)}
              </span>
            ) : undefined
          }
        >
          <InputNumber<string>
            min="0"
            precision={2}
            stringMode
            style={{ width: "100%" }}
            prefix="¥"
            placeholder="实际付款金额"
          />
        </Form.Item>
        <Form.Item
          name="payment_adjust_note"
          label="调整说明"
          dependencies={["payment_amount"]}
          rules={[
            { required: needNote, whitespace: true, message: ADJUST_NOTE_REQUIRED_TEXT },
            { max: 2000, message: "调整说明最多 2000 字" },
          ]}
        >
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            placeholder={needNote ? "实付与应付不一致的原因，如延迟发文、限流" : "实付与应付一致时可不填"}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
