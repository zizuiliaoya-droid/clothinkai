import { useEffect, useMemo, useState } from "react";
import { Alert, Divider, Form, Modal, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { shipPush } from "@/features/promotion/api";
import type { GateMissingItem, UiAction } from "@/features/flow/keys";
import { missingToFields } from "@/features/flow/missingToFields";
import type { Promotion, PromotionShipPushRequest } from "@/features/promotion/types";
import { GoodsItemsFields } from "./GoodsItemsFields";
import {
  itemFieldName,
  itemsChanged,
  itemsInitial,
  itemsPayload,
  legacyPreselectStyle,
  membersOf,
  type ItemValues,
} from "./goodsItemsForm";
import { ReceiverFields } from "./ReceiverFields";
import { RECEIVER_FIELDS, receiverInitial, receiverPatch, receiverPhoneError, type ReceiverValues } from "./receiverForm";
import { useFlowFeedback } from "./useFlowFeedback";

export interface ShipPushTarget {
  row: Promotion;
  /** 打开时的 `ui.actions.ship_push`：enabled + missing = 缺项都能在这里补。 */
  action: UiAction;
}

type Props = {
  target: ShipPushTarget | null;
  onClose: () => void;
};

type Values = ReceiverValues & { items: ItemValues };

/** 缺项 key → 表单字段；goods_items 由调用处按空着的成员行展开。 */
const FIELD_MAP: Partial<Record<string, string>> = Object.fromEntries(RECEIVER_FIELDS.map((f) => [f, f]));

/**
 * 确认推送仓库（流程线 8.4、3.3 S3）：收件三项（预填、可改）+ 每个成员一个颜色尺码 Select + 顶部缺项列表。
 * 弹窗里补的内容与推送同一事务；写完仍缺 → 422 FLOW_GATE_MISSING，按 key 标红，弹窗不关。
 */
export function ShipPushModal({ target, onClose }: Props) {
  const [form] = Form.useForm<Values>();
  const { refresh, handleError } = useFlowFeedback();
  const row = target?.row ?? null;
  const members = useMemo(() => (row ? membersOf(row) : []), [row]);
  const legacyStyle = row ? legacyPreselectStyle(row, members) : null;
  const [initial, setInitial] = useState<ReceiverValues | null>(null);
  const [missing, setMissing] = useState<GateMissingItem[]>([]);
  const [rest, setRest] = useState<string | null>(null);

  useEffect(() => {
    if (!target) return;
    const init = receiverInitial(target.row);
    setInitial(init);
    setMissing(target.action.missing ?? []);
    setRest(null);
    form.resetFields();
    form.setFieldsValue({ ...init, items: itemsInitial(target.row, members) });
  }, [target, members, form]);

  function markMissing(list: GateMissingItem[]) {
    setMissing(list);
    const { fields, rest: unmapped } = missingToFields(
      list.filter((m) => m.key !== "goods_items"),
      FIELD_MAP
    );
    if (list.some((m) => m.key === "goods_items")) {
      const values = (form.getFieldValue("items") ?? {}) as ItemValues;
      const empty = members.filter((m) => !values[m.style_id]);
      for (const m of empty.length ? empty : members) {
        fields.push({ name: itemFieldName(m.style_id), errors: ["必填"] });
      }
    }
    // 字段名是运行时拼的（items.<style_id>），表单的字面量类型对不上，按 setFields 的入参收
    form.setFields(fields as Parameters<typeof form.setFields>[0]);
    setRest(unmapped);
  }

  const mutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: PromotionShipPushRequest }) => shipPush(id, payload),
    onSuccess: () => {
      message.success("已推送仓库，进入待打单");
      refresh();
      onClose();
    },
    onError: (err) => {
      const phone = receiverPhoneError(err);
      if (phone) {
        form.setFields([{ name: "receiver_phone", errors: [phone] }]);
        return;
      }
      const gate = handleError(err);
      if (gate) markMissing(gate);
    },
  });

  return (
    <Modal
      title={row ? `确认推送仓库 · ${row.internal_code}` : "确认推送仓库"}
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="确认推送仓库"
      confirmLoading={mutation.isPending}
      destroyOnHidden
      width={560}
    >
      {missing.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
          message={`还缺：${missing.map((m) => m.label).join("、")}`}
          description="在下面补齐后推送，补的内容和推送一起保存。"
        />
      )}
      {rest && <Alert type="error" showIcon style={{ marginTop: 12 }} message={rest} />}
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(values) => {
          if (!row || !initial) return;
          const items = itemsPayload(members, values.items);
          if (!items) return;
          const payload: PromotionShipPushRequest = { ...receiverPatch(initial, values) };
          if (itemsChanged(row.items, items)) payload.items = items;
          mutation.mutate({ id: row.id, payload });
        }}
      >
        <ReceiverFields required />
        <Divider style={{ margin: "4px 0 12px" }} />
        <GoodsItemsFields
          form={form}
          members={members}
          legacy={legacyStyle && row?.legacy_color_spec ? { styleId: legacyStyle, spec: row.legacy_color_spec } : null}
        />
      </Form>
    </Modal>
  );
}
