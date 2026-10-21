import { Alert, Form, Input, Modal, Spin, message } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getPromotion, promotionDetailKey, startRecallPromotion } from "../api";
import type { Promotion } from "../types";
import { useRecallFreightFeedback } from "./useRecallFreightFeedback";

export function RecallModal({ target, onClose }: { target: Promotion | null; onClose: () => void }) {
  return target ? <RecallForm key={target.id} target={target} onClose={onClose} /> : null;
}
function RecallForm({ target, onClose }: { target: Promotion; onClose: () => void }) {
  const [form] = Form.useForm<{ recall_reason: string }>();
  const { data, isLoading, error } = useQuery({ queryKey: promotionDetailKey(target.id), queryFn: () => getPromotion(target.id) });
  const feedback = useRecallFreightFeedback(target.line_id);
  const action = data?.ui?.actions?.recall_start;
  const enabled = action?.state === "enabled";
  const mutation = useMutation({
    mutationFn: (reason: string) => startRecallPromotion(target.id, { recall_reason: reason.trim() }),
    onSuccess: () => { message.success("已发起召回"); feedback.refresh(); onClose(); },
    onError: feedback.handleError,
  });
  return <Modal title={`发起召回 · ${target.internal_code}`} open onCancel={onClose} onOk={() => form.submit()} okText="发起召回" okButtonProps={{ disabled: !enabled }} confirmLoading={mutation.isPending} destroyOnHidden>
    {isLoading && <Spin />}
    {error && <Alert type="error" message="单据详情加载失败，请关闭后重试" />}
    {data?.publish_status === "未发布" && <Alert type="warning" showIcon message="这张单还没发布，召回会同时取消推广" />}
    {data && !enabled && <Alert type="info" message={action?.hint ?? action?.reason ?? "当前单据不可发起召回"} />}
    <Form form={form} layout="vertical" style={{ marginTop: 16 }} onFinish={(value) => { if (enabled) mutation.mutate(value.recall_reason); }}>
      <Form.Item name="recall_reason" label="召回原因" rules={[{ required: true, whitespace: true, message: "请填写召回原因" }, { max: 2000, message: "最多 2000 字" }]}>
        <Input.TextArea rows={3} maxLength={2000} showCount disabled={!enabled || mutation.isPending} />
      </Form.Item>
    </Form>
  </Modal>;
}
