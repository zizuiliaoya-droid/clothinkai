import { useCallback, useMemo } from "react";
import { listBloggers } from "@/features/blogger/api";
import {
  bloggerOptionLabel,
  bloggerPickerParams,
  bloggerPickerQueryKey,
} from "@/features/blogger/picker";
import type { PickerOption } from "./mergeSelectedOption";
import {
  PICKER_PAGE_SIZE,
  RemoteSearchSelect,
  type RemoteSearchSelectProps,
} from "./RemoteSearchSelect";

async function fetchBloggerOptions(
  keyword: string | undefined,
  platform: string | undefined
): Promise<PickerOption[]> {
  const page = await listBloggers(bloggerPickerParams(keyword, platform, PICKER_PAGE_SIZE));
  return page.items.map((b) => ({ value: b.id, label: bloggerOptionLabel(b) }));
}

type Props = Omit<RemoteSearchSelectProps, "fetchOptions" | "queryKeyPrefix"> & {
  /** 只搜这个平台的博主（谈款页按表单的平台过滤，8b）；不传 = 全部平台。 */
  platform?: string;
};

/** 博主下拉：按昵称 / 账号 / 网页ID 服务端搜索（7a-2、8b）。 */
export function BloggerSelect({
  placeholder = "搜博主昵称、账号或网页ID",
  platform,
  ...props
}: Props) {
  const queryKeyPrefix = useMemo(() => bloggerPickerQueryKey(platform), [platform]);
  const fetchOptions = useCallback(
    (keyword?: string) => fetchBloggerOptions(keyword, platform),
    [platform]
  );
  return (
    <RemoteSearchSelect
      {...props}
      placeholder={placeholder}
      queryKeyPrefix={queryKeyPrefix}
      fetchOptions={fetchOptions}
    />
  );
}
