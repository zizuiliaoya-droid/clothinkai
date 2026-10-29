// BI Tab2 单品分析（7e R6，设计 2.6 / Q18）：商品下拉（选项 = 本区间单品表的商品）+ 单品 KPI 5 卡
// + 金额趋势 / 净投产趋势（/bi/goods/{id}/trend）+ 笔记散点（/bi/goods/{id}/notes）。
// 区间用 BI 响应的实际起止，单品块与趋势跟刷单开关；不显示商品编码（补充 2）。
import { Alert, Card, Col, Empty, Row, Select, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import dayjs from "dayjs";
import { ScatterChart, type ScatterDatum } from "@/components/Charts/ScatterChart";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import { getBiGoodsNotes, getBiGoodsTrend } from "../api";
import type { BiDashboard, TimeGranularity } from "../types";
import { goodsOptionLabels, money, moneyNumber, roiText } from "./biFormat";
import { KpiCard, MUTED } from "./StoreOverviewTab";

/** 散点为空（设计 2.6：生产只有少数单录了 7 天数据）。 */
const NOTES_EMPTY_TEXT = "还没有录 7 天数据的笔记";
/** 散点超 500 篇时后端只回最近 500 篇。 */
const NOTES_TRUNCATED_TEXT = "只显示最近 500 篇";
const NOTE_DETAIL_HINT = "暂无可访问的推广详情";

/** 趋势横轴：点上是桶首日；月 / 年取前缀。 */
function trendLabel(date: string, granularity: TimeGranularity): string {
  if (granularity === "year") return date.slice(0, 4);
  if (granularity === "month") return date.slice(0, 7);
  return date;
}

const formatDay = (v: number) => dayjs(v).format("YYYY-MM-DD");
const toNumberOrNull = (v: string | null) => (v == null ? null : Number(v));

interface Props {
  data: BiDashboard | undefined;
  loading: boolean;
  granularity: TimeGranularity;
  /** 筛选里的商品（URL / 记忆 / 用户选择）；不在本区间选项里时取第一项（不写记忆）。 */
  goodsId: string | undefined;
  onGoodsChange: (goodsId: string) => void;
}

export function GoodsAnalysisTab({ data, loading, granularity, goodsId, onGoodsChange }: Props) {
  const navigate = useNavigate();
  const items = data?.goods.items ?? [];
  const optionLabels = goodsOptionLabels(items);
  const selected = items.find((g) => g.goods_id === goodsId) ?? items[0];
  const missingSelection = !loading && !!data && !!goodsId && !items.some((item) => item.goods_id === goodsId);
  const selectionNotice = missingSelection ? <Alert type="info" showIcon
    message="所选商品不在当前筛选范围或可见结果中"
    description={items.length ? "当前显示本区间第一项，可重新选择商品。" : "可调整筛选后查看。"} /> : null;
  const selectedId = selected?.goods_id;
  const dateFrom = data?.date_from;
  const dateTo = data?.date_to;
  const excludeBrushing = data?.exclude_brushing ?? true;
  const enabled = !!selectedId && !!dateFrom && !!dateTo;

  const trendQuery = useQuery({
    queryKey: ["bi-goods", "trend", selectedId, dateFrom, dateTo, granularity, excludeBrushing],
    enabled,
    queryFn: () =>
      getBiGoodsTrend(selectedId!, {
        preset: "custom",
        date_from: dateFrom,
        date_to: dateTo,
        granularity,
        exclude_brushing: excludeBrushing,
      }),
  });
  const notesQuery = useQuery({
    queryKey: ["bi-goods", "notes", selectedId, dateFrom, dateTo],
    enabled,
    queryFn: () => getBiGoodsNotes(selectedId!, { date_from: dateFrom!, date_to: dateTo! }),
  });

  if (!loading && data && items.length === 0) {
    return <>{selectionNotice}<Empty description="这段时间没有单品数据" /></>;
  }

  const points = trendQuery.data?.points ?? [];
  const labels = points.map((p) => trendLabel(p.date, granularity));
  const notes = notesQuery.data;
  const detailUrls = new Map((notes?.points ?? []).map((p) => [p.promotion_id, p.detail_url]));
  const scatter: ScatterDatum[] = (notes?.points ?? []).map((p) => ({
    key: p.promotion_id,
    label: `${p.blogger_nickname}（${p.platform}）`,
    x: dayjs(p.publish_date).valueOf(),
    y: p.like_count,
    size: toNumberOrNull(p.promo_cost),
  }));

  const kpis = [
    { title: "支付金额", value: money(selected?.pay_amount) },
    { title: "退款金额", value: money(selected?.refund_amount) },
    {
      title: "净投产",
      value: roiText(selected?.net_roi),
      description: excludeBrushing ? "已扣除刷单" : "未扣除刷单",
    },
    { title: "站内花费", value: money(selected?.internal_spend) },
    { title: "站外花费", value: money(selected?.external_spend) },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {selectionNotice}
      <Space wrap size={8}>
        <span>商品：</span>
        <Select<string>
          showSearch
          aria-label="选择商品"
          optionFilterProp="label"
          value={selectedId}
          loading={loading}
          style={{ width: 320, maxWidth: "100%" }}
          options={items.map((g, i) => ({ value: g.goods_id, label: optionLabels[i] }))}
          onChange={onGoodsChange}
        />
      </Space>

      <Card title="单品经营" loading={loading} styles={{ body: { background: "#f8fafc" } }}>
        <Row gutter={[12, 12]}>
          {kpis.map((k) => (
            <Col key={k.title} xs={24} sm={12} lg={8} xl={4}>
              <KpiCard {...k} />
            </Col>
          ))}
        </Row>
      </Card>

      {trendQuery.isError ? (
        <Alert type="error" showIcon message="单品趋势加载失败，请稍后重试" />
      ) : null}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="金额趋势" loading={loading || trendQuery.isLoading} style={{ height: "100%" }}>
            {/* 跟着卡片宽度画：1440 宽下半宽卡正文约 503，按固定 720 画会藏掉后段、末刻度截半（第 2 轮浏览器复核） */}
            <MiniLineChart
              fluid
              ariaLabel="单品金额趋势折线图"
              labels={labels}
              series={[
                { name: "支付金额", color: "#4f46e5", data: points.map((p) => Number(p.pay_amount)) },
                { name: "退款金额", color: "#ef4444", data: points.map((p) => Number(p.refund_amount)) },
                { name: "站内花费", color: "#0ea5e9", data: points.map((p) => Number(p.ad_spend)) },
                { name: "站外花费", color: "#f97316", data: points.map((p) => Number(p.promo_cost)) },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="净投产趋势" loading={loading || trendQuery.isLoading} style={{ height: "100%" }}>
            <MiniLineChart
              fluid
              ariaLabel="单品净投产趋势折线图"
              labels={labels}
              formatValue={(v) => v.toFixed(2)}
              series={[
                { name: "净投产", color: "#16a34a", data: points.map((p) => toNumberOrNull(p.net_roi)) },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card title="笔记点赞散点" loading={loading || notesQuery.isLoading}>
        <Typography.Paragraph style={{ color: MUTED }}>
          横轴发布日期、纵轴 7 天点赞，点越大站外推广成本越高；只画录过 7 天数据的已发布笔记。
        </Typography.Paragraph>
        {notesQuery.isError ? (
          <Alert type="error" showIcon message="笔记散点加载失败，请稍后重试" />
        ) : (
          <>
            {notes?.truncated ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message={`${NOTES_TRUNCATED_TEXT}（按发布日期）`}
              />
            ) : null}
            <ScatterChart
              title="笔记点赞散点"
              data={scatter}
              labelHeader="博主"
              xLabel="发布日期"
              yLabel="7 天点赞"
              sizeLabel="站外推广成本"
              formatX={formatDay}
              formatY={(v) => Math.round(v).toLocaleString("zh-CN")}
              formatSize={moneyNumber}
              canSelect={(d) => Boolean(detailUrls.get(d.key))}
              onSelect={(d) => { const url = detailUrls.get(d.key); if (url) navigate(url); }}
              renderLabel={(d) => {
                const url = detailUrls.get(d.key);
                return url ? <Link to={url}>{d.label}</Link> : d.label;
              }}
              pointTitle={(d) =>
                `${d.label}：${formatDay(d.x as number)}，点赞 ${d.y}（${detailUrls.get(d.key) ? "查看推广详情" : NOTE_DETAIL_HINT}）`
              }
              emptyText={NOTES_EMPTY_TEXT}
            />
          </>
        )}
      </Card>
    </div>
  );
}
