import { useEffect, useMemo } from "react";
import { Form, Modal, Typography, message, theme } from "antd";
import { useMutation } from "@tanstack/react-query";
import { putItems } from "@/features/promotion/api";
import type { GoodsItemIn, Promotion } from "@/features/promotion/types";
import { GoodsItemsFields } from "./GoodsItemsFields";
import { itemsChanged, itemsInitial, itemsPayload, legacyPreselectStyle, membersOf, type ItemValues } from "./goodsItemsForm";
import { useFlowFeedback } from "./useFlowFeedback";

type Props = {
  /** 要改颜色尺码的推广单（`ui.edits` 含 goods_items 才会打开；推送后只有管理员有）；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 改颜色尺码（流程线 8.4）：只改颜色尺码、不换商品；整组替换（PUT /{id}/items）。 */
export function ItemsModal({ target, onClose }: Props) {
  const { token } = theme.useToken();
  const [form] = Form.useForm<{ items: ItemValues }>();
  const { refresh, handleError } = useFlowFeedback();
  const members = useMemo(() => (target ? membersOf(target) : []), [target]);
  const legacyStyle = target ? legacyPreselectStyle(target, members) : null;

  useEffect(() => {
    if (!target) return;
    form.resetFields();
    form.setFieldsValue({ items: itemsInitial(target, members) });
  }, [target, members, form]);

  const mutation = useMutation({
    mutationFn: ({ id, items }: { id: string; items: GoodsItemIn[] }) => putItems(id, items),
    onSuccess: () => {
      message.success("颜色尺码已更新");
      refresh();
      onClose();
    },
    onError: (err) => handleError(err),
  });

  return (
    <Modal
      title={target ? `改颜色尺码 · ${target.internal_code}` : "改颜色尺码"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      confirmLoading={mutation.isPending}
      destroyOnHidden
    >
      <Typography.Paragraph style={{ marginTop: 16, color: token.colorTextSecondary }}>
        只改颜色尺码，不换商品；要换商品请用「改归属商品」。
      </Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => {
          if (!target) return;
          const items = itemsPayload(members, values.items);
          if (!items) return;
          if (!itemsChanged(target.items, items)) {
            message.info("没有改动");
            onClose();
            return;
          }
          mutation.mutate({ id: target.id, items });
        }}
      >
        <GoodsItemsFields
          form={form}
          members={members}
          legacy={legacyStyle && target?.legacy_color_spec ? { styleId: legacyStyle, spec: target.legacy_color_spec } : null}
        />
      </Form>
    </Modal>
  );
}
