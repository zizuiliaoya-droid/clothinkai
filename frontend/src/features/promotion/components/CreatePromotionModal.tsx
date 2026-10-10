import { useEffect, useState } from "react";
import {
  Checkbox,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { createPromotion } from "@/features/promotion/api";
import { buildCreatePayload } from "@/features/promotion/createPayload";
import type { PromotionCreate } from "@/features/promotion/types";
import { listGoodsForStyle, type GoodsOption } from "@/features/product/api";
import { goodsNameLabel } from "@/features/promotion/goodsLabel";
import { COOPERATION_MODES, COOPERATION_MODE_HINT } from "@/features/promotion/listConstants";
import { extractErrorMessage } from "@/services/apiClient";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { StyleSelect } from "@/components/RemoteSelect/StyleSelect";
import { PLATFORMS } from "@/features/common/platforms";

type Props = {
  open: boolean;
  onClose: () => void;
};

/** 新建推广。 */
export function CreatePromotionModal({ open, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();
  // 新建推广时选中的款式 → 拉它归属的商品。只有一个就自动填，多个才需要人工选。
  const [formStyleId, setFormStyleId] = useState<string | null>(null);

  const { data: formGoods } = useQuery({
    queryKey: ["goods", "by-style", formStyleId],
    enabled: !!formStyleId,
    queryFn: () => listGoodsForStyle(formStyleId!),
  });
  // 只显示商品名 + 套装标记，不显示商品编码（业务方 10-06）
  const goodsOptions = (formGoods ?? []).map((g: GoodsOption) => ({
    label: goodsNameLabel(g),
    value: g.goods_main_id,
  }));
  // 款式只归属一个商品时不必打扰用户，直接用它
  const goodsChoiceNeeded = (formGoods?.length ?? 0) > 1;

  // 每次打开都从空表单开始
  useEffect(() => {
    if (open) form.resetFields();
  }, [open, form]);

  // 选完款式后自动带出商品；有歧义时清空让用户显式选
  useEffect(() => {
    if (!formGoods) return;
    form.setFieldsValue({
      goods_main_id: formGoods.length === 1 ? formGoods[0].goods_main_id : undefined,
    });
  }, [formGoods, form]);

  const createMutation = useMutation({
    mutationFn: (values: PromotionCreate) => createPromotion(values),
    onSuccess: () => {
      message.success("推广已创建");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function handleCreate(values: Record<string, unknown>) {
    createMutation.mutate(buildCreatePayload(values));
  }

  return (
    <Modal
      title="新建推广"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={createMutation.isPending}
      destroyOnHidden
      width={560}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={handleCreate}
        style={{ marginTop: 16 }}
        initialValues={{ platform: "小红书", cooperation_date: dayjs(), need_shipping: false }}
      >
        <Form.Item
          name="style_id"
          label="款式"
          rules={[{ required: true, message: "请选择款式" }]}
        >
          <StyleSelect onChange={(v) => setFormStyleId(v ?? null)} />
        </Form.Item>
        {goodsChoiceNeeded && (
          <Form.Item
            name="goods_main_id"
            label="归属商品"
            tooltip="这个款式既单卖又进了套装，推广费要算给哪个商品由你决定"
            rules={[{ required: true, message: "请选择这次推广归属的商品" }]}
          >
            <Select placeholder="选择归属商品" options={goodsOptions} />
          </Form.Item>
        )}
        <Form.Item
          name="blogger_id"
          label="博主"
          rules={[{ required: true, message: "请选择博主" }]}
        >
          <BloggerSelect />
        </Form.Item>
        <Form.Item
          name="cooperation_mode"
          label="合作模式"
          rules={[{ required: true, message: "请选择合作模式" }]}
          tooltip="决定样品成本与博主服务费怎么算，以及审核通过后走哪个流程。单据建好后不能改。"
        >
          <Select
            placeholder="选择合作模式"
            options={COOPERATION_MODES.map((m) => ({
              label: `${m} · ${COOPERATION_MODE_HINT[m]}`,
              value: m,
            }))}
          />
        </Form.Item>
        <Space size="large">
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true }]}
          >
            <Select
              style={{ width: 160 }}
              options={PLATFORMS.map((p) => ({ label: p, value: p }))}
            />
          </Form.Item>
          <Form.Item
            name="cooperation_date"
            label="合作日期"
            rules={[{ required: true, message: "请选择合作日期" }]}
          >
            <DatePicker style={{ width: 180 }} />
          </Form.Item>
        </Space>
        <Form.Item
          name="quote_amount"
          label="报价金额"
          tooltip="置换模式没有博主服务费，填了也会被置 0"
        >
          <InputNumber
            min={0}
            precision={2}
            style={{ width: "100%" }}
            placeholder="报价（可选）"
            prefix="¥"
          />
        </Form.Item>
        <Form.Item name="note_title" label="笔记标题">
          <Input placeholder="笔记标题（可选）" />
        </Form.Item>
        <Form.Item name="remark" label="备注">
          <Input.TextArea rows={2} placeholder="备注（可选）" />
        </Form.Item>
        {/* 默认不勾（11-58）：补录历史单不进发货；事后要发货可在操作菜单「纳入发货」 */}
        <Form.Item
          name="need_shipping"
          valuePropName="checked"
          style={{ marginBottom: 0 }}
          extra="补录历史单不用勾；勾了才进待推送仓库"
        >
          <Checkbox>需要仓库发货</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
}
