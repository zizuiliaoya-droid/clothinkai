/** 远程搜索下拉的一项：value 是实体 id，label 是给人看的名字。 */
export interface PickerOption {
  value: string;
  label: string;
}

/**
 * 把「已选值」补进当前搜索结果（7a-2）。
 *
 * 服务端搜索每次只回当前关键词的前 20 条，已选的那项多半不在里面；antd Select 在
 * options 里找不到 value 时只能显示原始 UUID。所以已选值不在结果里时，把它放到最前：
 * - pinned：用户刚在下拉里选中的那项（换了关键词也记得名字），优先；
 * - selected：父组件给的回显（如编辑谈款时单据上带的博主昵称）。
 *
 * 两者的 value 都必须等于当前 value 才插：表单被重置、换了一张单据时，旧的那项不能冒出来。
 * value 为空不插；结果按 value 去重（先到先得）；不改入参。
 */
export function mergeOptions(
  results: readonly PickerOption[],
  value: string | null | undefined,
  pinned?: PickerOption | null,
  selected?: PickerOption | null
): PickerOption[] {
  const seen = new Set<string>();
  const merged: PickerOption[] = [];
  for (const option of results) {
    if (seen.has(option.value)) continue;
    seen.add(option.value);
    merged.push(option);
  }
  if (!value || seen.has(value)) return merged;
  if (pinned && pinned.value === value) return [pinned, ...merged];
  if (selected && selected.value === value) return [selected, ...merged];
  return merged;
}
