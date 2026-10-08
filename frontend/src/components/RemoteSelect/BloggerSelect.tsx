import { listBloggers } from "@/features/blogger/api";
import type { PickerOption } from "./mergeSelectedOption";
import {
  PICKER_PAGE_SIZE,
  RemoteSearchSelect,
  type RemoteSearchSelectProps,
} from "./RemoteSearchSelect";

// 存的是选项数组；不要和 BloggerListPage 的 ["bloggers", 筛选条件]（分页原始数据）共用 key
const QUERY_KEY_PREFIX = ["bloggers", "picker-options"] as const;

async function fetchBloggerOptions(keyword?: string): Promise<PickerOption[]> {
  const page = await listBloggers({ page: 1, page_size: PICKER_PAGE_SIZE, keyword });
  return page.items.map((b) => ({
    value: b.id,
    label: `${b.nickname} (${b.xiaohongshu_id})`,
  }));
}

type Props = Omit<RemoteSearchSelectProps, "fetchOptions" | "queryKeyPrefix">;

/** 博主下拉：按昵称 / 小红书号服务端搜索（7a-2）。 */
export function BloggerSelect({ placeholder = "搜博主昵称或小红书号", ...props }: Props) {
  return (
    <RemoteSearchSelect
      {...props}
      placeholder={placeholder}
      queryKeyPrefix={QUERY_KEY_PREFIX}
      fetchOptions={fetchBloggerOptions}
    />
  );
}
