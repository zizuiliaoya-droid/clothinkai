import { useState, type AriaAttributes, type CSSProperties } from "react";
import { Select } from "antd";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { mergeOptions, type PickerOption } from "./mergeSelectedOption";
import { useDebouncedValue } from "./useDebouncedValue";

/** 每次搜索取几条。后端 /api/styles/、/api/bloggers/ 的 page_size 上限是 100，下拉里 20 条够挑。 */
export const PICKER_PAGE_SIZE = 20;

/** 关键词最长多少字（后端 keyword 上限 128，这里留足余量）。 */
const KEYWORD_MAX_LENGTH = 64;
const DEBOUNCE_MS = 300;

export interface RemoteSearchSelectProps extends AriaAttributes {
  /** Form.Item 会注入 id，转给 Select 才能让 label 指到输入框。 */
  id?: string;
  value?: string | null;
  onChange?: (value: string | undefined) => void;
  /** 按关键词取选项；关键词为空表示「不带条件的前几条」。 */
  fetchOptions: (keyword?: string) => Promise<PickerOption[]>;
  /** React Query 的 key 前缀，后面接关键词。缓存的是选项数组，别和存分页原始数据的 key 共用。 */
  queryKeyPrefix: readonly unknown[];
  /** 已选值的回显（如编辑时单据上带的名字）；已选值不在搜索结果里时用它显示名字。 */
  selected?: PickerOption | null;
  placeholder?: string;
  allowClear?: boolean;
  disabled?: boolean;
  style?: CSSProperties;
}

/**
 * 服务端搜索的下拉（7a-2）：博主 2,000+、款式上百个，不能一次拉全量再本地过滤。
 *
 * - 只在展开时请求；关键词去首尾空白、截 64 字后防抖 300ms，清空关键词立刻回到默认列表
 * - 换关键词时保留上一批结果（keepPreviousData），不闪空
 * - 已选值不在当前结果里时补进去显示名字（mergeOptions），选中后换了关键词也不会变回 UUID
 */
export function RemoteSearchSelect({
  value,
  onChange,
  fetchOptions,
  queryKeyPrefix,
  selected,
  placeholder,
  allowClear,
  disabled,
  style,
  ...rest
}: RemoteSearchSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [pinned, setPinned] = useState<PickerOption | null>(null);

  const typed = search.trim().slice(0, KEYWORD_MAX_LENGTH);
  const debounced = useDebouncedValue(typed, DEBOUNCE_MS);
  const keyword = typed === "" ? "" : debounced;

  const { data, isFetching } = useQuery({
    queryKey: [...queryKeyPrefix, keyword],
    queryFn: () => fetchOptions(keyword || undefined),
    enabled: open,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  const options = mergeOptions(data ?? [], value, pinned, selected);
  const notFoundContent = isFetching ? "搜索中…" : keyword ? "没有匹配" : "输入关键词搜索";

  return (
    <Select<string>
      {...rest}
      showSearch
      filterOption={false}
      value={value ?? undefined}
      options={options}
      placeholder={placeholder}
      allowClear={allowClear}
      disabled={disabled}
      style={style}
      loading={isFetching}
      notFoundContent={notFoundContent}
      onSearch={setSearch}
      onOpenChange={(next) => {
        setOpen(next);
        // 收起时清掉关键词：下次展开从默认列表开始，不会出现「输入框是空的、列表却是上次搜的」
        if (!next) setSearch("");
      }}
      onChange={(next) => {
        setPinned(options.find((o) => o.value === next) ?? null);
        onChange?.(next);
      }}
    />
  );
}
