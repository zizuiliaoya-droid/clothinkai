import { useEffect, useRef, useState } from "react";
import { Alert, Button, DatePicker, Form, Input, InputNumber, Modal, Select, Space, message } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { StyleSelect } from "@/components/RemoteSelect/StyleSelect";
import { PLATFORMS } from "@/features/common/platforms";
import type { GateMissingItem } from "@/features/flow/keys";
import { listGoodsForStyle, type GoodsOption } from "@/features/product/api";
import { GoodsItemsFields } from "@/features/promotion/components/GoodsItemsFields";
import { goodsNameLabel } from "@/features/promotion/goodsLabel";
import { COOPERATION_MODES, COOPERATION_MODE_HINT } from "@/features/promotion/listConstants";
import type { GoodsMember } from "@/features/promotion/types";
import {
  createNegotiation,
  negotiationGoodsMembers,
  othersInProgress,
  submitNegotiation,
  updateNegotiation,
} from "../api";
import { othersInProgressLines } from "../negotiationDialogs";
import {
  GOODS_REBUILT_HINT,
  SUBMIT_GATE_LABEL,
  membersFromItems,
  missingFieldsFor,
  negotiationFormInitial,
  negotiationPayload,
  negotiationSubmitMissing,
  rebuildOnGoodsChange,
  type NegotiationFormValues,
} from "../negotiationForm";
import type { Negotiation } from "../types";
import { useNegotiationFeedback } from "./useNegotiationFeedback";

export interface NegotiationFormTarget {
  /** 编辑的谈款（草稿 / 老板退回）；null = 新建。 */
  editing: Negotiation | null;
  /** 打开时就要标红的缺项（菜单「提交审核」带 missing、或提交 422 时）。 */
  missing?: GateMissingItem[] | null;
}

type Props = {
  /** null = 弹窗关着。 */
  target: NegotiationFormTarget | null;
  onClose: () => void;
};

type Mode = "draft" | "submit";

/** 「保存并提交」是两次请求：记下败在哪一步（提交被打回时谈款已经存下了）。 */
class StageError extends Error {
  readonly stage: "save" | "submit";
  readonly err: unknown;
  constructor(stage: "save" | "submit", err: unknown) {
    super(stage);
    this.stage = stage;
    this.err = err;
  }
}

/**
 * 谈款新建 / 编辑（流程线 8.3）：博主 → 款式 → 商品（多个才出下拉）→ 每个成员一行颜色尺码 → 模式、平台、约定发布、服务费、备注。
 * 「保存草稿」不卡缺项；「保存并提交」先在前端查 ★¹，缺的标红不发请求；后端 422 的 missing 同样标回字段。
 * 老板退回的单顶部只读显示退回意见；同一博主别的 PR 在谈时顶部黄色提示（不拦）。不显示商品编码与 SKU 编码（规-1）。
 */
export function NegotiationFormModal({ target, onClose }: Props) {
  const [form] = Form.useForm<NegotiationFormValues>();
  const { refresh, handleError, notifySubmitted } = useNegotiationFeedback();
  const open = !!target;
  const editing = target?.editing ?? null;

  const [members, setMembers] = useState<GoodsMember[]>([]);
  const membersRef = useRef<GoodsMember[]>([]);
  const [rebuilt, setRebuilt] = useState(false);
  const [missing, setMissing] = useState<GateMissingItem[]>([]);
  const [rest, setRest] = useState<string | null>(null);
  /** 新建后再点「保存并提交」被 422 打回时，后续保存改走编辑。 */
  const [savedId, setSavedId] = useState<string | null>(null);

  const bloggerId = Form.useWatch("blogger_id", form) as string | undefined;
  const styleId = Form.useWatch("style_id", form) as string | undefined;
  const formGoods = Form.useWatch("goods_main_id", form) as string | undefined;
  const platform = Form.useWatch("platform", form) as string | undefined;
  const mode = Form.useWatch("cooperation_mode", form) as string | undefined;

  function setMembersBoth(next: GoodsMember[]) {
    membersRef.current = next;
    setMembers(next);
  }

  function markMissing(list: readonly GateMissingItem[], rows: readonly GoodsMember[]) {
    setMissing([...list]);
    const items = form.getFieldValue("items") as NegotiationFormValues["items"];
    const { fields, rest: unmapped } = missingFieldsFor(list, rows, items);
    // 明细字段名是运行时拼的（items.<style_id>），表单的字面量类型对不上，按 setFields 的入参收
    form.setFields(fields as Parameters<typeof form.setFields>[0]);
    setRest(unmapped);
  }

  // 每次打开从单据（或空表单）开始
  useEffect(() => {
    if (!target) return;
    const initialMembers = target.editing ? membersFromItems(target.editing) : [];
    setMembersBoth(initialMembers);
    setRebuilt(false);
    setSavedId(target.editing?.id ?? null);
    setMissing([]);
    setRest(null);
    form.resetFields();
    form.setFieldsValue(
      target.editing ? negotiationFormInitial(target.editing) : { platform: "小红书", items: {} }
    );
    if (target.missing?.length) markMissing(target.missing, initialMembers);
    // markMissing 只读 form 与 setter，按打开的单初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, form]);

  // 款式归属的商品：只有一个自动选中、不出下拉；同一款既单卖又进套装时必选
  const { data: goodsList } = useQuery({
    queryKey: ["goods", "by-style", styleId],
    enabled: open && !!styleId,
    queryFn: () => listGoodsForStyle(styleId!),
  });
  const goodsChoiceNeeded = (goodsList?.length ?? 0) > 1;
  useEffect(() => {
    if (goodsList?.length === 1 && form.getFieldValue("goods_main_id") !== goodsList[0].goods_main_id) {
      form.setFieldValue("goods_main_id", goodsList[0].goods_main_id);
    }
  }, [goodsList, form]);
  const goodsForMembers = goodsChoiceNeeded ? (formGoods ?? null) : (goodsList?.[0]?.goods_main_id ?? null);
  const membersEnabled = open && !!styleId && !!goodsList && (!goodsChoiceNeeded || !!formGoods);

  // 明细该有哪几行（套装按启用成员）；款式集合变了整组清空并提示
  const { data: membersData } = useQuery({
    queryKey: ["negotiation-goods-members", styleId, goodsForMembers],
    enabled: membersEnabled,
    queryFn: () => negotiationGoodsMembers(styleId!, goodsForMembers),
  });
  useEffect(() => {
    if (!open) return;
    let found: GoodsMember[] | null = null;
    if (membersData && membersEnabled) found = membersData.members;
    else if (goodsChoiceNeeded && !formGoods) found = [];
    if (!found) return;
    const next = found;
    const prev = membersRef.current;
    const values = form.getFieldValue("items") as NegotiationFormValues["items"];
    const result = rebuildOnGoodsChange(prev, next, values);
    if (result.rebuilt || prev.length !== next.length || prev.some((m, i) => m.style_id !== next[i]?.style_id)) {
      form.setFieldValue(
        "items",
        Object.fromEntries(next.map((m) => [m.style_id, result.items[m.style_id] ?? undefined]))
      );
      setMembersBoth(next);
    }
    if (result.rebuilt) setRebuilt(true);
  }, [open, membersData, membersEnabled, goodsChoiceNeeded, formGoods, form]);

  // 同一博主有别的 PR 在谈：只提示，不拦保存和提交（7c-16）
  const { data: others } = useQuery({
    queryKey: ["negotiation-others-in-progress", bloggerId, editing?.id ?? null],
    enabled: open && !!bloggerId,
    queryFn: () => othersInProgress(bloggerId!, editing?.id),
  });
  const otherLines = othersInProgressLines(others);

  const mutation = useMutation<
    { mode: Mode; saved: Negotiation },
    StageError,
    { mode: Mode; values: NegotiationFormValues }
  >({
    mutationFn: async ({ mode, values }) => {
      const rows = membersRef.current;
      const payload = negotiationPayload(values, rows);
      let saved: Negotiation;
      try {
        saved = savedId ? await updateNegotiation(savedId, payload) : await createNegotiation(payload);
      } catch (err) {
        throw new StageError("save", err);
      }
      setSavedId(saved.id);
      if (mode === "draft") return { mode, saved };
      try {
        return { mode, saved: await submitNegotiation(saved.id) };
      } catch (err) {
        throw new StageError("submit", err);
      }
    },
    onSuccess: ({ mode, saved }) => {
      refresh();
      if (mode === "submit") notifySubmitted(saved);
      else
        message.success(
          saved.cooperation_mode === "置换" ? "已保存。置换没有博主服务费，报价已置 0" : "已保存"
        );
      onClose();
    },
    onError: (e) => {
      // 提交被打回时谈款已经存下了，列表要刷新
      if (e.stage === "submit") refresh();
      const gate = handleError(e.err);
      if (gate) markMissing(gate, membersRef.current);
    },
  });

  async function save(mode: Mode) {
    let values: NegotiationFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    // validateFields 只回注册过的字段：只有一个商品时 goods_main_id 没有下拉，明细整组也在 store 里
    values = { ...(form.getFieldsValue(true) as NegotiationFormValues), ...values, items: form.getFieldValue("items") };
    if (mode === "submit") {
      const local = negotiationSubmitMissing(values, membersRef.current);
      if (local.length) {
        markMissing(local, membersRef.current);
        return;
      }
    }
    setMissing([]);
    setRest(null);
    mutation.mutate({ mode, values });
  }

  const returned = editing?.status === "老板退回";
  const pendingMode = mutation.isPending ? mutation.variables?.mode : null;

  return (
    <Modal
      title={editing ? "编辑谈款" : "新建谈款"}
      open={open}
      onCancel={onClose}
      destroyOnHidden
      width={640}
      footer={
        <Space wrap style={{ justifyContent: "flex-end" }}>
          <Button onClick={onClose}>取消</Button>
          <Button
            loading={pendingMode === "draft"}
            disabled={mutation.isPending && pendingMode !== "draft"}
            onClick={() => void save("draft")}
          >
            {returned ? "保存" : "保存草稿"}
          </Button>
          <Button
            type="primary"
            loading={pendingMode === "submit"}
            disabled={mutation.isPending && pendingMode !== "submit"}
            onClick={() => void save("submit")}
          >
            保存并提交
          </Button>
        </Space>
      }
    >
      {returned && editing?.final_review_opinion && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
          message={`老板退回意见（${editing.final_reviewer_name ?? "老板"}）`}
          description={editing.final_review_opinion}
        />
      )}
      {otherLines.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 12 }}
          message={otherLines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        />
      )}
      {missing.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          message={`还缺：${missing.map((m) => m.label).join("、")}`}
          description={rest ?? "补齐标红的项再提交；只保存草稿不用填全。"}
        />
      )}
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="blogger_id"
          label={SUBMIT_GATE_LABEL.blogger_id}
          rules={[{ required: true, message: "请选择博主" }]}
        >
          <BloggerSelect
            platform={platform}
            selected={
              editing ? { value: editing.blogger_id, label: editing.blogger_nickname || editing.blogger_id } : null
            }
          />
        </Form.Item>
        <Form.Item name="style_id" label="款式" rules={[{ required: true, message: "请选择款式" }]}>
          <StyleSelect
            selected={
              editing
                ? {
                    value: editing.style_id,
                    // 与下拉选项同一写法（货号 + 款名，8.8 货号保留）
                    label: `${editing.style_code ?? ""} ${editing.style_name ?? ""}`.trim() || editing.style_id,
                  }
                : null
            }
            onChange={() => form.setFieldValue("goods_main_id", undefined)}
          />
        </Form.Item>
        {goodsChoiceNeeded && (
          <Form.Item
            name="goods_main_id"
            label="商品"
            tooltip="这个款式既单卖又进了套装，谈的是哪个商品由你决定"
            rules={[{ required: true, message: "请选择商品" }]}
          >
            <Select
              placeholder="选择商品"
              options={(goodsList ?? []).map((g: GoodsOption) => ({
                label: goodsNameLabel(g),
                value: g.goods_main_id,
              }))}
            />
          </Form.Item>
        )}
        {rebuilt && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message={GOODS_REBUILT_HINT} />
        )}
        <GoodsItemsFields form={form} members={members} legacy={null} optional />
        <Form.Item
          name="cooperation_mode"
          label={SUBMIT_GATE_LABEL.cooperation_mode}
          rules={[{ required: true, message: "请选择合作模式" }]}
          tooltip="决定样品成本与博主服务费怎么算。生成推广单后就锁定了。"
        >
          <Select
            placeholder="选择合作模式"
            options={COOPERATION_MODES.map((m) => ({ label: `${m} · ${COOPERATION_MODE_HINT[m]}`, value: m }))}
          />
        </Form.Item>
        <Space align="start" wrap style={{ display: "flex" }}>
          <Form.Item name="platform" label={SUBMIT_GATE_LABEL.platform} required style={{ width: 160 }}>
            <Select options={PLATFORMS.map((p) => ({ label: p, value: p }))} />
          </Form.Item>
          <Form.Item
            name="scheduled_publish_date"
            label={SUBMIT_GATE_LABEL.scheduled_publish_date}
            required
            style={{ width: 180 }}
          >
            <DatePicker
              style={{ width: "100%" }}
              disabledDate={(d) => d.isBefore(dayjs().startOf("day"))}
            />
          </Form.Item>
          <Form.Item
            name="quote_amount"
            label="博主服务费"
            tooltip="不填则取博主档案里的报价。置换模式填了也会被置 0。"
            extra={mode === "置换" ? "置换没有博主服务费，保存后置 0" : undefined}
            style={{ width: 180 }}
          >
            <InputNumber min={0} precision={2} style={{ width: "100%" }} prefix="¥" />
          </Form.Item>
        </Space>
        <Form.Item name="remark" label="备注">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
