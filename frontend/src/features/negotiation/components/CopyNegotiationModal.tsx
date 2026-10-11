import { Modal, Typography, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { copyNegotiation } from "../api";
import { COPY_CONFIRM_TEXT } from "../negotiationDialogs";
import type { Negotiation } from "../types";
import type { NegotiationDialogTarget } from "./ManagerReviewModal";
import { useNegotiationFeedback } from "./useNegotiationFeedback";

type Props = {
  /** 菜单「复制为新谈款」打开（只在审批未通过、本人）；null = 弹窗关着。 */
  target: NegotiationDialogTarget | null;
  onClose: () => void;
  /** 复制成功：页面接着打开新草稿的编辑弹窗。 */
  onCopied: (created: Negotiation) => void;
};

/** 复制为新谈款（N10）：确认后生成新草稿，原单不动。 */
export function CopyNegotiationModal({ target, onClose, onCopied }: Props) {
  const { refresh, handleError } = useNegotiationFeedback();

  const mutation = useMutation({
    mutationFn: (id: string) => copyNegotiation(id),
    onSuccess: (created) => {
      message.success("已复制为新草稿，改完提交");
      refresh();
      onClose();
      onCopied(created);
    },
    onError: (err) => {
      handleError(err);
    },
  });

  return (
    <Modal
      title="复制为新谈款"
      open={!!target}
      onCancel={onClose}
      onOk={() => target && mutation.mutate(target.row.id)}
      okText="复制"
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Typography.Paragraph style={{ marginTop: 16 }}>{COPY_CONFIRM_TEXT}。</Typography.Paragraph>
      {target?.row.review_opinion && (
        <Typography.Paragraph style={{ marginBottom: 0 }}>
          驳回意见：{target.row.review_opinion}
        </Typography.Paragraph>
      )}
    </Modal>
  );
}
