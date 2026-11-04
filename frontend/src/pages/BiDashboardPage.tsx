import { useReadOnlySupport } from "@/stores/supportStore";
import { useRef } from "react";
import { Alert, Button, Card, Select, Space, Switch, Tabs, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { getBiDashboard } from "@/features/report/api";
import { biImageFilename, useBiImageExport } from "@/features/report/bi/exportBiImage";
import { GoodsAnalysisTab } from "@/features/report/bi/GoodsAnalysisTab";
import { PersonalView } from "@/features/report/bi/PersonalView";
import { StoreOverviewTab } from "@/features/report/bi/StoreOverviewTab";
import { ReportForbidden } from "@/features/report/components/ReportForbidden";
import { TIME_PRESETS, type ReportFilterSchema } from "@/features/report/reportFilters";
import type { TimeGranularity, TimePreset } from "@/features/report/types";
import { useReportCapabilities } from "@/features/report/useReportCapabilities";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";

const GRANULARITY_OPTIONS: Array<{
  label: string;
  value: TimeGranularity;
}> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

type BiTab = "store" | "goods";
const BI_TABS: readonly BiTab[] = ["store", "goods"];

/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";

/**
 * 筛选状态：preset / granularity / 刷单开关记忆；日期区间只从 URL 带入、不记；
 * granularity 不在 URL 白名单里，只走记忆。刷单开关管单品块（与单品趋势），店铺块是全店口径、不跟它。
 * 当前 Tab 与 Tab2 选中的商品（7e R6）url + 记忆；URL 带来的不写记忆，用户切 Tab / 选商品才写。
 */
interface BiFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  granularity: TimeGranularity;
  exclude_brushing: boolean;
  tab: BiTab;
  goods_id?: string;
}

const BI_FILTER_SCHEMA: ReportFilterSchema<BiFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
  granularity: {
    kind: "enum",
    values: GRANULARITY_OPTIONS.map((o) => o.value),
    url: false,
    memory: true,
  },
  exclude_brushing: { kind: "boolean", url: true, memory: true },
  tab: { kind: "enum", values: BI_TABS, url: true, memory: true },
  goods_id: { kind: "uuid", url: true, memory: true },
};

const BI_FILTER_DEFAULTS: BiFilters = {
  preset: "last_30d",
  granularity: "day",
  exclude_brushing: true,
  tab: "store",
};

export function BiDashboardPage() {
  const readOnly = useReadOnlySupport();
  const { filters, ready, setFilters } = useReportFilters(
    "bi_dashboard",
    BI_FILTER_SCHEMA,
    BI_FILTER_DEFAULTS,
    true // 同一 /bi 路径的新query也是新落点，不写筛选记忆。
  );
  const { preset, granularity, exclude_brushing: excludeBrushing, tab } = filters;
  const range = toDateRange(filters.date_from, filters.date_to);

  const { dateFrom, dateTo, enabled } = useReportTimeRange(preset, range);
  const { data: caps, isError: capsError } = useReportCapabilities();
  // 按 caps.bi 分版（7e R6）：full = 完整版；personal（只有 read_own）= 只渲染个人版、不请求 /bi；
  // null = 无权限页。能力没回来前两个版本都不查，免得 PR 先打一次 /bi 拿 403。
  const biCap = caps?.bi;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["bi-dashboard", preset, dateFrom, dateTo, granularity, excludeBrushing],
    // 等偏好回填完再查，避免先用默认值查一次、回填后又查一次。
    enabled: enabled && ready && biCap === "full",
    queryFn: () =>
      getBiDashboard({
        preset,
        date_from: dateFrom,
        date_to: dateTo,
        granularity,
        exclude_brushing: excludeBrushing,
      }),
  });

  const periodText = data ? `${data.date_from} 至 ${data.date_to}` : "";
  // 导出图片截当前 Tab 的内容（不含筛选栏与 Tab 标签）
  const storeRef = useRef<HTMLDivElement>(null);
  const goodsRef = useRef<HTMLDivElement>(null);
  const { exporting, run: exportImage } = useBiImageExport();

  if (caps === undefined) {
    return capsError ? (
      <Alert
        type="error"
        showIcon
        message="BI 看板加载失败"
        description="读取当前账号的报表权限失败，请刷新页面重试。"
      />
    ) : (
      <Card title="BI 看板" loading />
    );
  }
  if (biCap == null) return <ReportForbidden name="BI 看板" />;

  const cardTitle = (
    <ReportCardTitle
      title="BI 看板"
      // 整页一次来源：区间每天都有汇总覆盖才读汇总表，否则显示「实时数据」
      freshness={
        <ReportFreshness
          preset={preset}
          dateFrom={dateFrom}
          dateTo={dateTo}
          enabled={enabled && ready}
        />
      }
    />
  );
  const rangeFilter = (
    <ReportTimeRangeFilter
      preset={preset}
      onPresetChange={(v) => setFilters({ preset: v })}
      range={range}
      onRangeChange={(v) => setFilters(fromDateRange(v))}
    />
  );

  if (biCap === "personal") {
    return (
      <Card title={cardTitle}>
        <Space style={{ marginBottom: 16 }} wrap size={8}>
          {rangeFilter}
        </Space>
        <PersonalView preset={preset} dateFrom={dateFrom} dateTo={dateTo} enabled={enabled && ready} />
      </Card>
    );
  }

  return (
    <Card title={cardTitle}>
      <Space style={{ marginBottom: 8 }} wrap size={8}>
        {rangeFilter}
        <span>趋势粒度：</span>
        <Select<TimeGranularity>
          aria-label="BI 趋势粒度"
          value={granularity}
          style={{ width: 110 }}
          options={GRANULARITY_OPTIONS}
          onChange={(v) => setFilters({ granularity: v })}
        />
        <span style={{ marginLeft: 12 }}>剔除刷单：</span>
        <Switch
          aria-label="剔除刷单"
          checked={excludeBrushing}
          onChange={(v) => setFilters({ exclude_brushing: v })}
        />
        {!readOnly && <Button
          icon={<DownloadOutlined />}
          loading={exporting}
          disabled={!data}
          onClick={() =>
            data &&
            exportImage(
              (tab === "goods" ? goodsRef : storeRef).current,
              biImageFilename(tab, data.date_from, data.date_to)
            )
          }
        >
          导出图片
        </Button>}
      </Space>
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ color: MUTED }}>
          当前单店/租户汇总{periodText ? ` · ${periodText}` : ""} · 剔除刷单只影响单品 ROI，店铺经营按全店口径
        </Typography.Text>
      </div>

      {isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="BI 数据加载失败"
          description="请稍后重试，或检查当前账号的 BI 看板权限。"
        />
      ) : null}

      <Tabs
        activeKey={tab}
        onChange={(key) => setFilters({ tab: key as BiTab })}
        items={[
          {
            key: "store",
            label: "店铺总览",
            children: (
              <div ref={storeRef}>
                <StoreOverviewTab data={data} loading={isLoading} caps={caps} />
              </div>
            ),
          },
          {
            key: "goods",
            label: "单品分析",
            children: (
              <div ref={goodsRef}>
                <GoodsAnalysisTab
                  data={data}
                  loading={isLoading}
                  granularity={granularity}
                  goodsId={filters.goods_id}
                  onGoodsChange={(id) => setFilters({ goods_id: id })}
                />
              </div>
            ),
          },
        ]}
      />
    </Card>
  );
}
