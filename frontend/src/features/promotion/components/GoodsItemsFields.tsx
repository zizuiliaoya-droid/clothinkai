// 颜色尺码明细的表单项（ShipPushModal、ItemsModal 共用）：每个成员款式一个 Select，选项只显示「颜色 / 尺码」（规-1，不显示 SKU 编码）。
// 没有明细的旧单：legacy_color_spec 能对上推广单款式的某个 SKU 就预选，并标「按录入信息预选，请核对」。

import { useEffect, useState } from "react";
import { Form, Select, Tooltip, theme, type FormInstance } from "antd";
import { useQueries } from "@tanstack/react-query";
import { listSkusByStyle } from "@/features/product/api";
import type { GoodsMember } from "@/features/promotion/types";
import { colorSizeLabel } from "@/features/promotion/shipDisplay";
import { itemFieldName } from "./goodsItemsForm";
import { matchLegacyColorSpec } from "./matchLegacyColorSpec";

type Props = {
  form: FormInstance;
  members: GoodsMember[];
  /** 旧单预选：对哪个款式按原文预选、原文是什么；不预选传 null。 */
  legacy: { styleId: string; spec: string } | null;
  disabled?: boolean;
};

export function GoodsItemsFields({ form, members, legacy, disabled }: Props) {
  const { token } = theme.useToken();
  const [preselected, setPreselected] = useState<string | null>(null);
  const queries = useQueries({
    queries: members.map((m) => ({
      queryKey: ["skus-by-style", m.style_id],
      queryFn: () => listSkusByStyle(m.style_id),
      staleTime: 60_000,
    })),
  });

  const legacyIndex = legacy ? members.findIndex((m) => m.style_id === legacy.styleId) : -1;
  const legacySkus = legacyIndex >= 0 ? queries[legacyIndex]?.data : undefined;

  const legacyStyleId = legacy?.styleId ?? null;
  const legacySpec = legacy?.spec ?? null;
  // SKU 加载完、这一行还空着才预选；弹窗 destroyOnHidden，每次打开都重来
  useEffect(() => {
    if (!legacyStyleId || !legacySpec || !legacySkus) return;
    const name = itemFieldName(legacyStyleId);
    if (form.getFieldValue(name)) return;
    const hit = matchLegacyColorSpec(legacySpec, legacySkus);
    if (hit) {
      form.setFieldValue(name, hit);
      setPreselected(legacyStyleId);
    }
  }, [legacyStyleId, legacySpec, legacySkus, form]);

  return (
    <>
      {members.map((m, i) => {
        const q = queries[i];
        const options = (q?.data ?? []).map((s) => ({ label: colorSizeLabel(s.color, s.size), value: s.id }));
        const label = (
          <Tooltip title={m.goods_title !== m.display_short_name ? m.goods_title : undefined}>
            <span>{members.length > 1 ? `颜色尺码 · ${m.display_short_name}` : "颜色尺码"}</span>
          </Tooltip>
        );
        return (
          <Form.Item
            key={m.style_id}
            name={itemFieldName(m.style_id)}
            label={label}
            rules={[{ required: true, message: "必填" }]}
            extra={
              preselected === m.style_id ? (
                <span style={{ color: token.colorTextSecondary }}>按录入信息预选，请核对</span>
              ) : undefined
            }
            style={{ marginBottom: 12 }}
          >
            <Select
              aria-label={members.length > 1 ? `${m.display_short_name} 颜色尺码` : "颜色尺码"}
              placeholder="选择颜色 / 尺码"
              showSearch
              optionFilterProp="label"
              loading={q?.isLoading}
              disabled={disabled}
              options={options}
              notFoundContent={q?.isError ? "颜色尺码加载失败" : "该款暂无颜色尺码，请先在商品成本表维护"}
              onChange={() => {
                if (preselected === m.style_id) setPreselected(null);
              }}
            />
          </Form.Item>
        );
      })}
    </>
  );
}
