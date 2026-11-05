import { useCallback, useMemo } from "react";
import { listBloggers } from "@/features/blogger/api";
import { safeHomepageUrl } from "@/features/blogger/display";
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
  return page.items.map((b) => ({ value: b.id, label: bloggerOptionLabel(b), homepageUrl: b.homepage_url }));
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
      renderOption={(option) => {
        const href = safeHomepageUrl(option.homepageUrl);
        return <span style={{ display: "flex", gap: 12, justifyContent: "space-between" }}>
          <span>{option.label}</span>
          {href && <a href={href} target="_blank" rel="noopener noreferrer"
            aria-label={`打开 ${option.label} 的主页（新窗口）`}
            onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
            onClick={(event) => event.stopPropagation()}>打开主页</a>}
        </span>;
      }}
    />
  );
}
