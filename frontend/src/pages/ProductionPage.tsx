import { useState } from "react";
import {
  Button,
  Card,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
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
  PRODUCTION_FILTER_DEFAULTS,
  PRODUCTION_FILTER_SCHEMA,
} from "@/features/report/productionFilters";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";
import type { ProductionRow, TimeGranularity } from "@/features/report/types";
import { goodsDisplayName } from "@/features/product/api";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { extractErrorMessage } from "@/services/apiClient";
import {
  canExportReport,
  useReportCapabilities,
} from "@/features/report/useReportCapabilities";
import {
  buildProductionColumns,
  productionColumnsWidth,
  productionRowKey,
} from "@/features/report/productionColumns";
import { ProductionInactiveLinkNotice } from "@/features/report/components/ProductionCells";
import { ReportForbidden } from "@/features/report/components/ReportForbidden";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";

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
  // 筛选：URL（下钻 / 分享链接）→ 记忆 → 默认；手动改才写记忆（节流）
  const { filters, ready, setFilters } = useReportFilters(
    "product_roi",
    PRODUCTION_FILTER_SCHEMA,
    PRODUCTION_FILTER_DEFAULTS
  );
  const { preset, season, exclude_brushing: excludeBrushing } = filters;
  const range = toDateRange(filters.date_from, filters.date_to);
  const [trendGoods, setTrendGoods] = useState<ProductionRow | null>(null);
  const [trendGranularity, setTrendGranularity] = useState<TimeGranularity>("day");

  // 季节选项由报表侧提供（投产读权限即可，主管也能拿到，8a-3）；类目筛选已下线
  const { data: seasons } = useQuery({
    queryKey: ["reports", "season-options"],
    queryFn: getReportSeasonOptions,
  });
  const seasonOptions = (seasons ?? []).map((s) => ({ label: s, value: s }));

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

  const { data, isLoading, error } = useQuery({
    queryKey: ["production", preset, df, dt, excludeBrushing, season],
    // 等偏好回填完再查，避免先用默认值查一次、回填后又查一次。
    enabled: enabled && ready,
    // 403 重试没有意义，直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
    queryFn: () =>
      getProduction({
        preset,
        date_from: df,
        date_to: dt,
        exclude_brushing: excludeBrushing,
        season: season.length ? season : undefined,
      }),
  });
  // 导出按钮按 capabilities 出现（持 report.export:read 且能读这张表）；能力没回来前不显示
  const { data: reportCaps } = useReportCapabilities();
  const canExport = canExportReport(reportCaps, "production");
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

  // 列定义与导出共用（features/report/productionColumns.ts ↔ 后端 production_columns.py）
  const baseColumns = buildProductionColumns({
    recent7Range: data?.recent7_range,
    // 毛利三列只给管理员（capabilities.production_margin）；能力没回来前不渲染
    showMargin: reportCaps?.production_margin ?? false,
    onTrend: setTrendGoods,
  });

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
  const scrollX = productionColumnsWidth(baseColumns) + extraColumns.length * 130;
  const trendPoints = trend?.points ?? [];
  const latestTrendPoint = trendPoints.length
    ? trendPoints[trendPoints.length - 1]
    : undefined;

  // 菜单按 capabilities 已隐藏；直接输入 URL 进来、接口 403 时给明确提示，不显示空表
  if (isForbiddenError(error)) {
    return <ReportForbidden name="单品投产" />;
  }

  return (
    <Card
      title={
        <ReportCardTitle
          title="单品投产"
          freshness={
            <Space size={12} wrap>
              <ProductionInactiveLinkNotice count={data?.inactive_link_count} />
              <ReportFreshness
                preset={preset}
                dateFrom={df}
                dateTo={dt}
                enabled={enabled && ready}
              />
            </Space>
          }
        />
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <ReportTimeRangeFilter
          preset={preset}
          onPresetChange={(v) => setFilters({ preset: v })}
          range={range}
          onRangeChange={(v) => setFilters(fromDateRange(v))}
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
          onChange={(v: string[]) => setFilters({ season: v })}
        />
        <span style={{ marginLeft: 12 }}>剔除刷单：</span>
        <Switch
          aria-label="剔除刷单"
          checked={excludeBrushing}
          onChange={(v) => setFilters({ exclude_brushing: v })}
        />
        {canExport && (
          <Button
            icon={<DownloadOutlined />}
            loading={exportMutation.isPending}
            disabled={!enabled}
            onClick={() => exportMutation.mutate()}
          >
            导出
          </Button>
        )}
      </Space>
      <Table
        rowKey={productionRowKey}
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
            ? `投产趋势 · ${goodsDisplayName(trendGoods.goods_title, trendGoods.goods_short_name)}`
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
                name: "实际销售金额",
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
