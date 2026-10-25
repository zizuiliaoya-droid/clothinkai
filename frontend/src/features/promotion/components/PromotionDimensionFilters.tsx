import { useState } from "react";
import { Button, Checkbox, Select, Space, Tag, Typography } from "antd";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { UNASSIGNED_PR } from "@/features/report/reportFilters";
import { getPromotionFilterOptions } from "../api";
import type { PromotionDimensions } from "../promotionFilters";

interface Props {
  value: PromotionDimensions;
  onChange: (patch: Partial<PromotionDimensions>) => void;
}

export function PromotionDimensionFilters({ value, onChange }: Props) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const { data, isFetching, isError } = useQuery({
    queryKey: ["promotions", "filter-options", keyword, page, value.goods_id],
    queryFn: () => getPromotionFilterOptions({ keyword: keyword || undefined, page, selected_id: value.goods_id }),
    placeholderData: keepPreviousData,
  });
  const options = [...(data?.items ?? [])];
  if (data?.selected && !options.some((o) => o.id === data.selected!.id)) options.push(data.selected);
  return (
    <Space wrap style={{ marginBottom: 16, maxWidth: "100%" }}>
      <Select<string>
        aria-label="商品筛选" placeholder="商品" allowClear showSearch filterOption={false}
        style={{ width: 240, maxWidth: "100%" }} value={value.goods_id}
        loading={isFetching} searchValue={keyword}
        options={options.map((o) => ({ value: o.id, label: o.display_name }))}
        onSearch={(text) => { setKeyword(text.slice(0, 64)); setPage(1); }}
        onChange={(goods_id) => onChange({ goods_id })}
        notFoundContent={isError ? "商品选项加载失败" : undefined}
        popupRender={(menu) => (
          <>
            {menu}
            <Space style={{ padding: 8 }}>
              <Button size="small" disabled={page === 1 || isFetching} onClick={() => setPage((p) => p - 1)}>上一页</Button>
              <Typography.Text>{page} / {Math.max(1, Math.ceil((data?.total ?? 0) / 50))}</Typography.Text>
              <Button size="small" disabled={isFetching || page * 50 >= (data?.total ?? 0)} onClick={() => setPage((p) => p + 1)}>下一页</Button>
            </Space>
          </>
        )}
      />
      <Select<string[]>
        aria-label="季节筛选" placeholder="季节" mode="multiple" allowClear maxCount={20} maxTagCount={1}
        style={{ width: 200, maxWidth: "100%" }} value={value.season}
        options={(data?.seasons ?? []).map((s) => ({ value: s, label: s }))}
        onChange={(season) => onChange({ season })}
      />
      <Checkbox checked={value.pr_id === UNASSIGNED_PR} onChange={(e) => onChange({ pr_id: e.target.checked ? UNASSIGNED_PR : undefined })}>未分配 PR</Checkbox>
      {value.pr_id && value.pr_id !== UNASSIGNED_PR ? (
        <Tag closable onClose={() => onChange({ pr_id: undefined })}>已限定 PR</Tag>
      ) : null}
    </Space>
  );
}
