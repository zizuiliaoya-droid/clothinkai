import { useEffect, useState } from "react";
import { Alert, Form, Modal, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { updatePromotion } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { ReceiverFields } from "./ReceiverFields";
import {
  receiverInitial,
  receiverPatch,
  receiverPhoneError,
  type ReceiverValues,
} from "./receiverForm";
import { useFlowFeedback } from "./useFlowFeedback";

type Props = {
  /** 要改收件信息的推广单（`ui.edits` 含 receiver 才会打开）；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 改收件三项（流程线 8.4）：只交改过的项，清空给 null；电话由后端规范化。 */
export function ReceiverModal({ target, onClose }: Props) {
  const [form] = Form.useForm<ReceiverValues>();
  const { refresh, handleError } = useFlowFeedback();
  const [initial, setInitial] = useState<ReceiverValues | null>(null);

  useEffect(() => {
    if (!target) return;
    const init = receiverInitial(target);
    setInitial(init);
    form.resetFields();
    form.setFieldsValue(init);
  }, [target, form]);

  const mutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ReturnType<typeof receiverPatch> }) =>
      updatePromotion(id, patch),
    onSuccess: () => {
      message.success("收件信息已更新");
      refresh();
      onClose();
    },
    onError: (err) => {
      const phone = receiverPhoneError(err);
      if (phone) {
        form.setFields([{ name: "receiver_phone", errors: [phone] }]);
        return;
      }
      handleError(err);
    },
  });

  return (
    <Modal
      title={target ? `改收件信息 · ${target.internal_code}` : "改收件信息"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      {target?.ship_status === "待打单" && (
        // 「地址已更新」标记要等事件表（PR-4），这一版仓库页还不会标，所以提示人工告知
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="已推送仓库：仓库页会显示改后的收件信息，但暂不标「地址已更新」，改完请告知仓库。"
        />
      )}
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(values) => {
          if (!target || !initial) return;
          const patch = receiverPatch(initial, values);
          if (Object.keys(patch).length === 0) {
            message.info("没有改动");
            onClose();
            return;
          }
          mutation.mutate({ id: target.id, patch });
        }}
      >
        <ReceiverFields required={false} />
      </Form>
    </Modal>
  );
}
