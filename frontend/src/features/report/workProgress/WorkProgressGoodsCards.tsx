// 工作进度单品卡片（7e R5，报表视图设计 2.5）：前 12 个商品（约稿量降序），点商品名下钻该商品在区间内的推广单。
// 「未归属商品」不上卡片，只在「查看全部」弹窗的最后一页末尾；页面文字里不出现商品编码。
import { Button, Card, Col, Empty, Row, Spin, Typography } from "antd";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import type { DrillPromotionTarget } from "../drill";
import type { WorkProgressGoodsPage, WorkProgressGoodsRow } from "../types";
import { money, pct } from "./workProgressColumns";
import { goodsDrillTarget } from "./workProgressGoods";

/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: MUTED }}>{label}</div>
      <div style={{ fontSize: 16, color: "#0f172a", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
    </div>
  );
}

function GoodsCard({
  row,
  onOpen,
}: {
  row: WorkProgressGoodsRow;
  onOpen: () => void;
}) {
  return (
    <Card size="small" style={{ height: "100%" }}>
      <Button
        type="link"
        style={{
          padding: 0,
          height: "auto",
          maxWidth: "100%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontWeight: 600,
        }}
        aria-label={`查看 ${row.display_name} 的推广单`}
        onClick={onOpen}
      >
        <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />
      </Button>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: "8px 12px",
          marginTop: 8,
        }}
      >
        <Metric label="约稿量" value={row.quote_count} />
        <Metric label="发布量" value={row.publish_count} />
        <Metric label="完成率" value={pct(row.month_complete_rate)} />
        <Metric label="超时量" value={row.overdue_count} />
        <Metric label="点赞数" value={row.like_count} />
        <Metric label="推广成本" value={money(row.promo_cost)} />
      </div>
    </Card>
  );
}

interface Props {
  data: WorkProgressGoodsPage | undefined;
  loading: boolean;
  /** 当前季节（没选为 undefined），下钻带上。 */
  season: string[] | undefined;
  onDrill: (target: DrillPromotionTarget) => void;
  onShowAll: () => void;
}

export function WorkProgressGoodsCards({ data, loading, season, onDrill, onShowAll }: Props) {
  const items = data?.items ?? [];
  // 没归属商品的单只在「查看全部」里，所以只要有它也给入口
  const hasAny = items.length > 0 || data?.unassigned != null;
  return (
    <section aria-label="单品汇总" style={{ marginBottom: 24 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }}>
          单品汇总
          {data ? (
            <span style={{ marginLeft: 8, fontSize: 13, fontWeight: 400, color: MUTED }}>
              共 {data.total} 个商品
            </span>
          ) : null}
        </Typography.Title>
        {hasAny && <Button onClick={onShowAll}>查看全部</Button>}
      </div>
      <Spin spinning={loading}>
        {items.length > 0 ? (
          <Row gutter={[12, 12]}>
            {items.map((row) => (
              <Col key={row.goods_id ?? row.display_name} xs={24} sm={12} lg={8} xl={6}>
                <GoodsCard row={row} onOpen={() => data && onDrill(goodsDrillTarget(row, data, season))} />
              </Col>
            ))}
          </Row>
        ) : !data ? (
          <div style={{ minHeight: 80 }} />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              data?.unassigned
                ? "区间内的推广单都没有归属商品，点「查看全部」看汇总"
                : "区间内没有归属商品的推广单"
            }
          />
        )}
      </Spin>
    </section>
  );
}
