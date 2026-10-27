/** 仅放置人民币符号，不改变调用处既有精度、千分位或数值计算。 */
export function formatMoney(value: string | null | undefined): string {
  if (value == null) return "—";
  return value.startsWith("-") ? `-¥${value.slice(1)}` : `¥${value}`;
}
