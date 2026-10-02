import type { ReactNode } from "react";
import { Button, Space, Tooltip, Typography, message } from "antd";
import {
  ClockCircleOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { getSummaryFreshness, refreshSummaries } from "@/features/report/api";
import type { ReportFreshness as Freshness, TimePreset } from "@/features/report/types";
import { extractErrorMessage, isApiError } from "@/services/apiClient";

/**
 * 读汇总表的报表查询。手动刷新会重写全部汇总表，刷新完把这些查询一起作废，
 * 页面立刻按新数据重新取数。BI 看板这一期仍是全实时，不在其中。
 */
const SUMMARY_BACKED_QUERY_KEYS = [
  "production",
  "production-trend",
  "store-daily",
  "work-progress",
  "report-freshness",
] as const;

/** 同一租户已有刷新在进行中（每小时的定时刷新或别人刚点了刷新）。 */
const REFRESH_BUSY_CODE = "REPORT_SUMMARY_REFRESH_BUSY";

/**
 * 不用 antd 的 type="secondary"：那是 rgba(0,0,0,.45)，白底对比度约 3.4:1，不到 AA 的 4.5:1。
 * 这行字告诉用户数字有多旧，是要看清的信息。slate-600 约 7.6:1，视觉上仍是次要文字。
 *
 * 字号字重显式写死：放在卡片标题栏里会继承标题栏的 16px / 600，看起来像第二个标题。
 */
const LABEL_STYLE = { color: "#475569", fontSize: 14, fontWeight: 400 } as const;

const TITLE_ROW_STYLE = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  justifyContent: "space-between",
  columnGap: 16,
  rowGap: 4,
  paddingBlock: 8,
} as const;

/**
 * 报表卡片标题：左边页面标题，右边数据新鲜度；放不下时新鲜度换到第二行。
 *
 * 不用 Card 的 `extra` 插槽：extra 不换行、也不收缩，375px 宽时会把页面标题挤成
 * 「店铺数…」。
 */
export function ReportCardTitle({ title, freshness }: { title: string; freshness: ReactNode }) {
  return (
    <div style={TITLE_ROW_STYLE}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {title}
      </Typography.Title>
      {freshness}
    </div>
  );
}

function formatAsOf(iso: string): string {
  const t = dayjs(iso);
  const now = dayjs();
  if (t.isSame(now, "day")) return t.format("HH:mm");
  if (t.isSame(now, "year")) return t.format("MM-DD HH:mm");
  return t.format("YYYY-MM-DD HH:mm");
}

interface Props {
  preset: TimePreset;
  dateFrom?: string;
  dateTo?: string;
  /** 与页面报表查询用同一个 enabled：筛选还没就绪时不查，免得先按默认值闪一下。 */
  enabled?: boolean;
}

/**
 * 报表数据从哪来、截至几点。
 *
 * - 汇总表（每小时刷新）：显示「数据更新于 HH:mm」；有权限的人多一个「刷新」按钮
 * - 实时统计：显示「实时数据」，不给刷新按钮 —— 实时的数字本来就是最新的，
 *   这时刷新反而会把这段区间切到汇总表，以后就要等刷新才更新了
 */
export function ReportFreshness({ preset, dateFrom, dateTo, enabled = true }: Props) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["report-freshness", preset, dateFrom, dateTo],
    enabled,
    queryFn: () => getSummaryFreshness({ preset, date_from: dateFrom, date_to: dateTo }),
  });

  const refresh = useMutation({
    // 用服务端解析出的具体日期：预设模式（近 30 天等）下页面手里没有日期
    mutationFn: (fresh: Freshness) => refreshSummaries(fresh.date_from, fresh.date_to),
    onSuccess: async () => {
      await Promise.all(
        SUMMARY_BACKED_QUERY_KEYS.map((key) => queryClient.invalidateQueries({ queryKey: [key] }))
      );
      message.success("汇总数据已刷新");
    },
    onError: (error) => {
      if (isApiError(error) && error.response.data.code === REFRESH_BUSY_CODE) {
        message.warning("汇总数据正在刷新中，请稍后再试");
        return;
      }
      message.error(extractErrorMessage(error, "刷新失败"));
    },
  });

  if (!data) return null;

  if (data.source === "live") {
    return (
      <Tooltip title="这段时间没有完整的汇总数据，直接按明细实时统计">
        <Typography.Text style={LABEL_STYLE} role="status">
          <ThunderboltOutlined aria-hidden /> 实时数据
        </Typography.Text>
      </Tooltip>
    );
  }

  const asOf = data.data_as_of;
  return (
    // wrap：窄屏放不下时刷新按钮换到下一行。卡片标题栏是 overflow:hidden，
    // 不换行的话按钮会被截掉一半
    <Space size={8} wrap>
      <Tooltip
        title={
          <>
            {asOf ? `数据截至 ${dayjs(asOf).format("YYYY-MM-DD HH:mm:ss")}。` : null}
            汇总数据每小时自动更新，导入数据后相关日期会自动刷新。
          </>
        }
      >
        <Typography.Text style={LABEL_STYLE} role="status">
          <ClockCircleOutlined aria-hidden /> 数据更新于 {asOf ? formatAsOf(asOf) : "—"}
        </Typography.Text>
      </Tooltip>
      {data.can_refresh ? (
        <Button
          size="small"
          icon={<ReloadOutlined aria-hidden />}
          loading={refresh.isPending}
          onClick={() => refresh.mutate(data)}
        >
          刷新
        </Button>
      ) : null}
    </Space>
  );
}
