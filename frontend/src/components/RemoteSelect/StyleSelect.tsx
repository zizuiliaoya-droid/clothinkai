import { listStyles } from "@/features/product/api";
import type { PickerOption } from "./mergeSelectedOption";
import {
  PICKER_PAGE_SIZE,
  RemoteSearchSelect,
  type RemoteSearchSelectProps,
} from "./RemoteSearchSelect";

// 存的是选项数组。CostTablePage 已用 ["styles", "picker", 关键词] 缓存分页原始数据，
// 同一个 key 会让两边拿到对方形状的数据，所以另起一个
const QUERY_KEY_PREFIX = ["styles", "picker-options"] as const;

async function fetchStyleOptions(keyword?: string): Promise<PickerOption[]> {
  const page = await listStyles({ page: 1, page_size: PICKER_PAGE_SIZE, keyword });
  return page.items.map((s) => ({
    value: s.id,
    label: `${s.style_code} ${s.style_name}`,
  }));
}

type Props = Omit<RemoteSearchSelectProps, "fetchOptions" | "queryKeyPrefix">;

/** 款式下拉：按货号 / 款名 / 简称服务端搜索（7a-2）。 */
export function StyleSelect({ placeholder = "搜货号或款名", ...props }: Props) {
  return (
    <RemoteSearchSelect
      {...props}
      placeholder={placeholder}
      queryKeyPrefix={QUERY_KEY_PREFIX}
      fetchOptions={fetchStyleOptions}
    />
  );
}
