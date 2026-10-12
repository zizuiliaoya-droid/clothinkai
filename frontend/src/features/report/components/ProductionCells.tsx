// 单品投产表格里的单元格组件（7e R3）：列定义在 ../productionColumns.ts，这里只放要渲染 JSX 的格子。
import { Button, Tag } from "antd";
import { goodsDisplayName } from "@/features/product/api";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import type { ProductionRow } from "../types";

export function ProductionImageCell({ row }: { row: ProductionRow }) {
  return (
    <StyleImageThumbnail
      src={row.main_image_url}
      alt={`${goodsDisplayName(row.goods_title, row.goods_short_name)} 商品主图`}
    />
  );
}

/** 「商品」一列：不显示商品编码（补充 2）；导出 Excel 仍保留「商品编码」列。 */
export function ProductionGoodsCell({ row }: { row: ProductionRow }) {
  return (
    <>
      {row.is_suit && (
        <Tag color="purple" style={{ marginInlineEnd: 4 }}>
          套装
        </Tag>
      )}
      <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />
    </>
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
