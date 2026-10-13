// 结款重提弹窗（流程线 R7、8.4）：财务驳回结款单后，PR 写说明重提，结款单回到待付款、由财务重新填实付。
//
// - 只读显示财务的驳回原因（详情结款块的 reject_reason）
// - 说明必填（1 ~ 2000 字，全空白 = 没填）
// - 收款码 ★¹⁷ in_dialog：缺码时在这里补传（收款码在结款驳回时可换，7.3）；接口 422 FLOW_GATE_MISSING 时标出缺项
import { useEffect, useState } from "react";
import { Alert, Form, Input, Modal, Space, Spin, Typography, message } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { GateMissingItem, UiAction } from "@/features/flow/keys";
import { getPromotion, promotionDetailKey, resubmitSettlement } from "@/features/promotion/api";
import { paymentQrView } from "@/features/promotion/settlementFlow";
import type { Promotion } from "@/features/promotion/types";
import { PaymentQrEditor } from "./PaymentQrModal";
import { useFlowFeedback } from "./useFlowFeedback";

export const SETTLEMENT_RESUBMIT_NOTE_MAX = 2000;

export interface SettlementResubmitTarget {
  row: Promotion;
  /** 菜单项拿到的 ui.actions.settlement_resubmit：带 missing 时先在弹窗里补。 */
  action: UiAction;
}

type Props = {
  target: SettlementResubmitTarget | null;
  onClose: () => void;
};

export function SettlementResubmitModal({ target, onClose }: Props) {
  const [form] = Form.useForm<{ note: string }>();
  const feedback = useFlowFeedback();
  const [gateMissing, setGateMissing] = useState<GateMissingItem[] | null>(null);
  const id = target?.row.id ?? "";
  const { data, isLoading } = useQuery({
    queryKey: promotionDetailKey(id),
    queryFn: () => getPromotion(id),
    enabled: !!target,
  });

  // 每次打开清掉上一次接口报的缺项；表单在关闭时清空（close）
  useEffect(() => {
    if (target) setGateMissing(null);
  }, [target]);

  function close() {
    form.resetFields();
    onClose();
  }

  const mutation = useMutation({
    mutationFn: (note: string) => resubmitSettlement(id, { note }),
    onSuccess: () => {
      message.success("已重提，结款单回到待付款，等财务重新填实付");
      feedback.refresh();
      close();
    },
    onError: (err) => {
      const missing = feedback.handleError(err);
      if (missing) setGateMissing(missing);
    },
  });

  // 缺项：接口刚报的为准；否则看菜单项的 missing，补传收款码后详情里有码就不再提示
  const hasQr = Boolean(data?.payment_qr_attachment_id);
  const missing = (gateMissing ?? target?.action.missing ?? []).filter(
    (m) => !(m.key === "payment_qr" && hasQr),
  );
  const qrEditable = data ? paymentQrView(data).editable : false;

  return (
    <Modal
      title={target ? `结款重提 · ${target.row.internal_code}` : "结款重提"}
      open={!!target}
      onCancel={close}
      onOk={() => form.submit()}
      okText="重提结款"
      okButtonProps={{ disabled: missing.length > 0 }}
      confirmLoading={mutation.isPending}
      destroyOnHidden
      width={520}
    >
      {isLoading || !data ? (
        <Spin />
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%", marginTop: 8 }}>
          <div>
            <Typography.Text strong>财务驳回原因</Typography.Text>
            <div>
              <Typography.Text>{data.settlement?.reject_reason || "—"}</Typography.Text>
            </div>
          </div>
          {missing.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`缺：${missing.map((m) => m.label).join("、")}，补齐后才能重提`}
            />
          )}
          <div>
            <Typography.Text strong>博主收款码</Typography.Text>
            <PaymentQrEditor promotion={data} editable={qrEditable} />
          </div>
          <Form
            form={form}
            layout="vertical"
            onFinish={(v) => mutation.mutate(v.note.trim())}
          >
            <Form.Item
              name="note"
              label="重提说明"
              rules={[
                { required: true, whitespace: true, message: "请写重提说明" },
                { max: SETTLEMENT_RESUBMIT_NOTE_MAX, message: `不能超过 ${SETTLEMENT_RESUBMIT_NOTE_MAX} 字` },
              ]}
            >
              <Input.TextArea rows={3} placeholder="改了什么，财务会在时间线里看到" />
            </Form.Item>
          </Form>
        </Space>
      )}
    </Modal>
  );
}
