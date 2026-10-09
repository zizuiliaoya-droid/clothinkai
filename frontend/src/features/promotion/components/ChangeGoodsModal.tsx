import { useEffect } from "react";
import { Form, Modal, Select, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { updatePromotion } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { listGoodsForStyle, type GoodsOption } from "@/features/product/api";
import { goodsNameLabel } from "@/features/promotion/goodsLabel";
import { extractErrorMessage } from "@/services/apiClient";

type Props = {
  /** 要改归属的推广单；null = 弹窗关着。 */
  target: Promotion | null;
  onClose: () => void;
};

/** 改归属：目标推广 + 它所属款式的商品候选。 */
export function ChangeGoodsModal({ target, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm();

  const { data: targetGoods } = useQuery({
    queryKey: ["goods", "by-style", target?.style_id],
    enabled: !!target,
    queryFn: () => listGoodsForStyle(target!.style_id),
  });

  useEffect(() => {
    if (!target) return;
    form.resetFields();
    form.setFieldsValue({ goods_main_id: target.goods_main_id ?? undefined });
  }, [target, form]);

  const updateGoodsMutation = useMutation({
    mutationFn: ({ id, goods_main_id }: { id: string; goods_main_id: string }) =>
      updatePromotion(id, { goods_main_id }),
    onSuccess: () => {
      message.success("归属商品已更新，投产报表的推广费会跟着调整");
      onClose();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <Modal
      title={
        target
          ? `改归属商品 · ${target.style_code_snapshot} ${
              target.display_short_name ?? target.style_short_name_snapshot
            }`
          : "改归属商品"
      }
      open={!!target}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={updateGoodsMutation.isPending}
      destroyOnHidden
      width={520}
    >
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(values: { goods_main_id: string }) => {
          if (!target) return;
          updateGoodsMutation.mutate({
            id: target.id,
            goods_main_id: values.goods_main_id,
          });
        }}
      >
        <Form.Item
          name="goods_main_id"
          label="归属商品"
          tooltip="决定这笔推广费算给哪个商品的投产比。只能选包含该款式的商品。"
          rules={[{ required: true, message: "请选择归属商品" }]}
        >
          <Select
            placeholder="选择归属商品"
            options={(targetGoods ?? []).map((g: GoodsOption) => ({
              label: goodsNameLabel(g),
              value: g.goods_main_id,
            }))}
          />
        </Form.Item>
        {(targetGoods?.length ?? 0) <= 1 && (
          <Typography.Text type="secondary">
            该款式只归属一个商品，无需调整。
          </Typography.Text>
        )}
      </Form>
    </Modal>
  );
}
