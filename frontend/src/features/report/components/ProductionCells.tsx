// 单品投产表格里的单元格组件（7e R3）：列定义在 ../productionColumns.ts，这里只放要渲染 JSX 的格子。
import { Button, Tag, Tooltip, Typography } from "antd";
import { ExclamationCircleOutlined } from "@ant-design/icons";
import { goodsDisplayName } from "@/features/product/api";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import type { ProductionRow } from "../types";
import { HeaderHint } from "./HeaderHint";

export function ProductionImageCell({ row }: { row: ProductionRow }) {
  return (
    <StyleImageThumbnail
      src={row.main_image_url}
      alt={`${goodsDisplayName(row.goods_title, row.goods_short_name)} 商品主图`}
    />
  );
}

/** 次要文字：slate-600，白底约 7.6:1（antd secondary 只有约 3.4:1）。 */
const MUTED = "#475569";

/**
 * 「商品」一列：不显示商品编码（补充 2）；导出 Excel 仍保留「商品编码」列。
 * 第二行：渠道标签（一行 = 商品 × 渠道）+ 同渠道启用千牛链接 ≥ 2 条时「N 条链接」。
 */
export function ProductionGoodsCell({ row }: { row: ProductionRow }) {
  return (
    <>
      <div>
        {row.is_suit && (
          <Tag color="purple" style={{ marginInlineEnd: 4 }}>
            套装
          </Tag>
        )}
        <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />
      </div>
      <div style={{ marginTop: 2, fontSize: 12, color: MUTED }}>
        {row.channel && (
          <Tag
            color={row.channel === "直播" ? "magenta" : "blue"}
            style={{ marginInlineEnd: 4, fontSize: 12, lineHeight: "18px" }}
          >
            {row.channel}
          </Tag>
        )}
        {row.link_count >= 2 && <span>{row.link_count} 条链接</span>}
      </div>
    </>
  );
}

/**
 * 支付件数列头：注明剔除刷单开关不扣件数（件数始终含刷单）。
 * 说明挂在列头文字上（HeaderHint，Tab 停一次），不放问号图标：rc-table 测量行会克隆列头，图标会让 Tab 多停一次（R3 评审 L4）。
 */
export function ProductionPayOrdersTitle() {
  return <HeaderHint label="支付件数" hint="剔除刷单开关不扣件数" />;
}

const OFFSITE_EMPTY_HINT = "所选区间千牛与万相台没有同一天的数据";

/** 站外加购：null（区间内没有千牛与万相台同一天的数据）显示「—」并悬停说明；有数照出（0 也是数）。 */
export function ProductionOffsiteCartCell({ value }: { value: number | null }) {
  if (value != null) return <>{value}</>;
  return (
    <Tooltip title={OFFSITE_EMPTY_HINT} trigger={["hover", "focus"]}>
      <span
        data-testid="offsite-add-cart-empty"
        tabIndex={0}
        style={{ cursor: "help", borderBottom: `1px dotted ${MUTED}` }}
      >
        —
      </span>
    </Tooltip>
  );
}

/** 标题栏停用链接提示：区间内有日报或花费、但已停用的链接（未计入单品）；0 条不显示。 */
export function ProductionInactiveLinkNotice({ count }: { count: number | null | undefined }) {
  if (count == null || count <= 0) return null;
  return (
    // amber-700：白底约 5:1；图标只是装饰，文字本身说明
    <Typography.Text role="status" style={{ color: "#b45309", fontSize: 14, fontWeight: 400 }}>
      <ExclamationCircleOutlined aria-hidden /> {count} 条链接已停用、未计入单品
    </Typography.Text>
  );
}

export function ProductionTrendButton({
  row,
  onTrend,
}: {
  row: ProductionRow;
  onTrend: (row: ProductionRow) => void;
}) {
  return (
    <Button type="link" size="small" onClick={() => onTrend(row)}>
      折线图
    </Button>
  );
}
