import { useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  message,
} from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  exportReport,
  getProduction,
  getProductionTrend,
  getReportSeasonOptions,
} from "@/features/report/api";
import {
  restoreProductionFilters,
  toProductionMemory,
  type ProductionFilterMemory,
} from "@/features/report/productionFilters";
import type {
  ProductionRow,
  TimeGranularity,
  TimePreset,
} from "@/features/report/types";
import { goodsDisplayName } from "@/features/product/api";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import {
  ReportTimeRangeFilter,
  type ReportDateRange,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { useFilterMemory } from "@/features/preference/useFilterMemory";
import { extractErrorMessage } from "@/services/apiClient";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;
const TREND_GRANULARITY: Array<{ label: string; value: TimeGranularity }> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

function trendLabel(value: string, granularity: TimeGranularity): string {
  if (granularity === "year") return value.slice(0, 4);
  if (granularity === "month") return value.slice(0, 7);
  return value;
}

export function ProductionPage() {
  const [preset, setPreset] = useState<TimePreset>("last_30d");
  const [range, setRange] = useState<ReportDateRange>(null);
  const [excludeBrushing, setExcludeBrushing] = useState(true);
  const [season, setSeason] = useState<string[]>([]);
  const [trendGoods, setTrendGoods] = useState<ProductionRow | null>(null);
  const [trendGranularity, setTrendGranularity] = useState<TimeGranularity>("day");

  // 季节选项由报表侧提供（投产读权限即可，主管也能拿到，8a-3）；类目筛选已下线
  const { data: seasons } = useQuery({
    queryKey: ["reports", "season-options"],
    queryFn: getReportSeasonOptions,
  });
  const seasonOptions = (seasons ?? []).map((s) => ({ label: s, value: s }));

  // 筛选记忆：回填上次的选择，之后变更自动保存（节流）。
  const memory = useFilterMemory<ProductionFilterMemory>("product_roi");
  const restoredRef = useRef(false);
  useEffect(() => {
    if (!memory.ready || restoredRef.current) return;
    restoredRef.current = true;
    // 旧记录里的 category 与类型不对的值在这里被丢掉（后端读写也会剔 category）
    const saved = restoreProductionFilters(memory.restored);
    if (saved.preset) setPreset(saved.preset);
    if (saved.season) setSeason(saved.season);
    if (saved.exclude_brushing !== undefined) setExcludeBrushing(saved.exclude_brushing);
  }, [memory.ready, memory.restored]);

  useEffect(() => {
    // 回填完成前不要把默认值写回去，否则会把用户存的偏好冲掉。
    if (!restoredRef.current) return;
    memory.persist(
      toProductionMemory({ preset, season, exclude_brushing: excludeBrushing })
    );
    // memory.persist 每次渲染都是新函数，不进依赖，否则每次渲染都会触发保存。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, season, excludeBrushing]);

  const { dateFrom: df, dateTo: dt, enabled } = useReportTimeRange(preset, range);

  const { data: trend, isLoading: trendLoading } = useQuery({
    queryKey: [
      "production-trend",
      trendGoods?.goods_id,
      preset,
      df,
      dt,
      trendGranularity,
      excludeBrushing,
    ],
    enabled: !!trendGoods && enabled,
    queryFn: () =>
      getProductionTrend(trendGoods!.goods_id, {
        preset,
        date_from: df,
        date_to: dt,
        granularity: trendGranularity,
        exclude_brushing: excludeBrushing,
      }),
  });

  const { data, isLoading } = useQuery({
    queryKey: ["production", preset, df, dt, excludeBrushing, season],
    // 等偏好回填完再查，避免先用默认值查一次、回填后又查一次。
    enabled: enabled && memory.ready,
    queryFn: () =>
      getProduction({
        preset,
        date_from: df,
        date_to: dt,
        exclude_brushing: excludeBrushing,
        season: season.length ? season : undefined,
      }),
  });
  const exportMutation = useMutation({
    mutationFn: () =>
      exportReport("production", {
        preset,
        date_from: df,
        date_to: dt,
        exclude_brushing: excludeBrushing,
        season: season.length ? season : undefined,
      }),
    onSuccess: (filename) => message.success(`已导出 ${filename}`),
    onError: (error) => message.error(extractErrorMessage(error, "导出失败")),
  });

  // 列对齐 final.xlsx「投产报表」核心派生指标
  const baseColumns: ColumnsType<ProductionRow> = [
    {
      title: "主图",
      dataIndex: "main_image_url",
      width: 68,
      fixed: "left",
      render: (src: string | null, row) => (
        <StyleImageThumbnail src={src} alt={`${row.goods_code} 商品主图`} />
      ),
    },
    {
      title: "商品编码",
      dataIndex: "goods_code",
      width: 130,
      fixed: "left",
      render: (code: string, row: ProductionRow) =>
        row.is_suit ? (
          <Space size={4}>
            <span>{code}</span>
            <Tag color="purple">套装</Tag>
          </Space>
        ) : (
          code
        ),
    },
    {
      title: "商品简称",
      dataIndex: "goods_short_name",
      width: 180,
      ellipsis: { showTitle: false },
      render: (_: string | null, row: ProductionRow) => (
        <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />
      ),
    },
    {
      title: "含款号",
      dataIndex: "style_codes",
      width: 140,
      render: (codes: string[]) => (codes?.length ? codes.join("、") : "—"),
    },
    { title: "支付金额", dataIndex: "pay_amount", width: 110, render: money },
    { title: "退款金额", dataIndex: "refund_amount", width: 110, render: money },
    { title: "退货退款率", dataIndex: "return_rate", width: 110, render: pct },
    { title: "待确认收货金额", dataIndex: "confirmed_amount", width: 130, render: money },
    { title: "站外推广费", dataIndex: "promo_cost", width: 120, render: money },
    { title: "站内投放", dataIndex: "ad_spend", width: 110, render: money },
    { title: "推广总花费", dataIndex: "total_spend", width: 120, render: money },
    { title: "总加购数", dataIndex: "add_cart_count", width: 100 },
    { title: "加购成本", dataIndex: "add_cart_cost", width: 110, render: money },
    { title: "净投产比", dataIndex: "net_roi", width: 100, render: (v) => (v == null ? "—" : v) },
    { title: "推广单件成交成本", dataIndex: "unit_deal_cost", width: 150, render: money },
    {
      title: "趋势",
      key: "trend",
      width: 80,
      fixed: "right",
      render: (_: unknown, row: ProductionRow) => (
        <Button type="link" size="small" onClick={() => setTrendGoods(row)}>
          折线图
        </Button>
      ),
    },
  ];

  // 动态展开千牛/站内导入按款式汇总的其余指标（对齐 final.xlsx 投产报表全列）
  const extraKeys = Array.from(
    new Set(
      (data?.items ?? []).flatMap((r) => Object.keys(r.extra ?? {})),
    ),
  );
  const extraColumns: ColumnsType<ProductionRow> = extraKeys.map((k) => ({
    title: k,
    key: `extra_${k}`,
    width: 130,
    render: (_: unknown, row: ProductionRow) => {
      const v = (row.extra ?? {})[k];
      return v == null || v === "" ? "—" : String(v);
    },
  }));

  const columns = [...baseColumns, ...extraColumns];
  const scrollX = 1500 + extraColumns.length * 130;
  const trendPoints = trend?.points ?? [];
  const latestTrendPoint = trendPoints.length
    ? trendPoints[trendPoints.length - 1]
    : undefined;

  return (
    <Card
      title={
        <ReportCardTitle
          title="投产报表"
          freshness={
            <ReportFreshness
              preset={preset}
              dateFrom={df}
              dateTo={dt}
              enabled={enabled && memory.ready}
            />
          }
        />
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <ReportTimeRangeFilter
          preset={preset}
          onPresetChange={setPreset}
          range={range}
          onRangeChange={setRange}
        />
        <span style={{ marginLeft: 12 }}>季节/系列：</span>
        <Select
          aria-label="季节或系列"
          mode="multiple"
          value={season}
          style={{ minWidth: 180, maxWidth: 320 }}
          placeholder="全部"
          allowClear
          maxTagCount="responsive"
          options={seasonOptions}
          onChange={(v: string[]) => setSeason(v)}
        />
        <span style={{ marginLeft: 12 }}>剔除刷单：</span>
        <Switch
          aria-label="剔除刷单"
          checked={excludeBrushing}
          onChange={setExcludeBrushing}
        />
        <Button
          icon={<DownloadOutlined />}
          loading={exportMutation.isPending}
          disabled={!enabled}
          onClick={() => exportMutation.mutate()}
        >
          导出
        </Button>
      </Space>
      <Table
        rowKey="goods_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: scrollX }}
        pagination={false}
      />

      <Modal
        title={
          trendGoods
            ? `投产趋势 · ${trendGoods.goods_code} ${goodsDisplayName(trendGoods.goods_title, trendGoods.goods_short_name)}`
            : "投产趋势"
        }
        open={!!trendGoods}
        onCancel={() => setTrendGoods(null)}
        footer={null}
        width={780}
        destroyOnHidden
      >
        <Space style={{ marginBottom: 16 }} wrap>
          <span>统计单位：</span>
          <Select<TimeGranularity>
            aria-label="投产趋势统计单位"
            value={trendGranularity}
            style={{ width: 110 }}
            options={TREND_GRANULARITY}
            onChange={setTrendGranularity}
          />
        </Space>
        {latestTrendPoint && (
          <Card size="small" style={{ marginBottom: 16, maxWidth: 240 }}>
            <Statistic
              title={`最新 ROI（${trendLabel(latestTrendPoint.date, trendGranularity)}）`}
              value={
                latestTrendPoint.net_roi == null
                  ? "—"
                  : Number(latestTrendPoint.net_roi)
              }
              precision={latestTrendPoint.net_roi == null ? undefined : 2}
              suffix={latestTrendPoint.net_roi == null ? undefined : "x"}
            />
          </Card>
        )}
        {trendLoading ? (
          <div role="status" aria-live="polite" style={{ textAlign: "center", padding: 40 }}>
            加载中…
          </div>
        ) : (
          <MiniLineChart
            labels={trendPoints.map((point) =>
              trendLabel(point.date, trendGranularity)
            )}
            series={[
              {
                name: "支付金额",
                color: "#1677ff",
                data: trendPoints.map((point) => Number(point.pay_amount)),
              },
              {
                name: "退款金额",
                color: "#cf1322",
                data: trendPoints.map((point) => Number(point.refund_amount)),
              },
              {
                name: "确认金额",
                color: "#389e0d",
                data: trendPoints.map((point) => Number(point.confirmed_amount)),
              },
              {
                name: "站外推广费",
                color: "#722ed1",
                data: trendPoints.map((point) => Number(point.promo_cost)),
              },
              {
                name: "站内投放",
                color: "#fa8c16",
                data: trendPoints.map((point) => Number(point.ad_spend)),
              },
              {
                name: "总花费",
                color: "#08979c",
                data: trendPoints.map((point) => Number(point.total_spend)),
              },
            ]}
          />
        )}
      </Modal>
    </Card>
  );
}
