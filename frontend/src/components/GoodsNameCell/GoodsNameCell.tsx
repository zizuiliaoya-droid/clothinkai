import { Tag, Tooltip } from "antd";
import { goodsDisplayName } from "@/features/product/api";

type Props = {
  goodsTitle: string | null | undefined;
  shortName: string | null | undefined;
  /** 商品页用：没填简称时标出来，方便逐个补。报表里不标，免得满屏标签。 */
  markMissing?: boolean;
};

/**
 * 表格里的商品名：显示简称，没填回落全称；悬停看全称。
 *
 * 所在列要设 `ellipsis: { showTitle: false }`：截断交给列，提示交给这里的 Tooltip，
 * 否则浏览器原生 title 会和 Tooltip 叠在一起。
 */
export function GoodsNameCell({ goodsTitle, shortName, markMissing = false }: Props) {
  const name = goodsDisplayName(goodsTitle, shortName);
  const missing = !shortName;
  const fullTitle = goodsTitle ?? "";
  return (
    <Tooltip
      title={missing ? `还没填简称，显示的是全称：${fullTitle}` : `全称：${fullTitle}`}
      placement="topLeft"
    >
      <span>
        {markMissing && missing && <Tag style={{ marginInlineEnd: 4 }}>未填简称</Tag>}
        <span style={markMissing && missing ? { color: "#475569" } : undefined}>{name}</span>
      </span>
    </Tooltip>
  );
}
