// 收件三项的表单项（ReceiverModal、ShipPushModal 共用）。
import { Form, Input } from "antd";
import { RECEIVER_LABEL, RECEIVER_MAX_LEN } from "./receiverForm";

type Props = {
  /** 推送时三项都是 ★（必填）；改收件信息时可以清空。 */
  required: boolean;
};

export function ReceiverFields({ required }: Props) {
  const rules = required ? [{ required: true, whitespace: true, message: "必填" }] : [];
  return (
    <>
      <Form.Item name="receiver_name" label={RECEIVER_LABEL.receiver_name} rules={rules} style={{ marginBottom: 12 }}>
        <Input maxLength={RECEIVER_MAX_LEN.receiver_name} autoComplete="off" />
      </Form.Item>
      <Form.Item
        name="receiver_phone"
        label={RECEIVER_LABEL.receiver_phone}
        rules={rules}
        extra="11 位手机号（可带 +86），或 0 开头的座机"
        style={{ marginBottom: 12 }}
      >
        <Input maxLength={RECEIVER_MAX_LEN.receiver_phone} inputMode="tel" autoComplete="off" />
      </Form.Item>
      <Form.Item
        name="receiver_address"
        label={RECEIVER_LABEL.receiver_address}
        rules={rules}
        style={{ marginBottom: 12 }}
      >
        <Input.TextArea rows={2} maxLength={RECEIVER_MAX_LEN.receiver_address} showCount />
      </Form.Item>
    </>
  );
}
