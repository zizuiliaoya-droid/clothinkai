// BI 两个 Tab 共用的数字格式：金额两位小数加千分位，ROI 两位小数；null 显示「—」。

export const money = (value: string | null | undefined) =>
  value == null
    ? "—"
    : `¥${Number(value).toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
export const moneyNumber = (v: number) => money(String(v));
export const roiText = (value: string | null | undefined) =>
  value == null ? "—" : Number(value).toFixed(2);
