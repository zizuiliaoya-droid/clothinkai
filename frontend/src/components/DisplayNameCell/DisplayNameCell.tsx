import { Tooltip } from "antd";

type Props = {
  /** 要显示的名字：商品简称，或它的回落值（建单快照 / 商品全称）。空 → 「—」。 */
  name: string | null | undefined;
  /** 商品全称。有值且和 name 不同才给悬停提示。 */
  fullTitle?: string | null;
};

/**
 * 推广 / 仓库 / 催发 / 博主卡里的品名（7a-8）：显示短名，悬停看商品全称。
 *
 * 和 GoodsNameCell（商品页、报表用）分开：那边固定是「简称，没填回落全称」，
 * 这里的 name 由调用方给（品名回落的是建单快照，不是全称）。
 *
 * 所在列设 `ellipsis: { showTitle: false }` 时，提示交给这里：有全称走 Tooltip，
 * 没有全称就用原生 title 显示 name 本身，截断了也能看全。
 */
export function DisplayNameCell({ name, fullTitle }: Props) {
  if (!name) return <span>—</span>;
  if (fullTitle && fullTitle !== name) {
    return (
      <Tooltip title={`全称：${fullTitle}`} placement="topLeft">
        <span>{name}</span>
      </Tooltip>
    );
  }
  return <span title={name}>{name}</span>;
}
