// 工作进度周明细（7e R5，报表视图设计 2.5）：按合作日期的 ISO 周分桶，首尾周截到筛选区间，只出有单的周（升序）。
// 指标列与 PR 表共用；周行展开 = 该周实际起止 + 当前季节的推广单（不限 PR / 商品）。
import { DownOutlined, RightOutlined } from "@ant-design/icons";
import { Alert, Button, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { extractErrorMessage } from "@/services/apiClient";
import { DrillPromotionTable } from "../components/DrillPromotionModal";
import { drillTargetKey } from "../drill";
import type { WorkProgressWeekRow, WorkProgressWeeks } from "../types";
import { columnsWidth, workProgressMetricColumns } from "./workProgressColumns";
import { weekDrillTarget, weekPeriodLabel } from "./workProgressWeeks";
/** 展开按钮那一列的宽度（算横向滚动用）。 */
const EXPAND_COLUMN_WIDTH = 48;
interface Props {
  data: WorkProgressWeeks | undefined;
  loading: boolean;
  /** 取数失败（非 403；403 由页面整页提示）：出错误提示，照「查看全部」弹窗的写法。 */
  error?: unknown;
  /** 当前季节（没选为 undefined），展开下钻带上。 */
  season: string[] | undefined;
}
export function WorkProgressWeeksTable({ data, loading, error, season }: Props) {
  const columns: ColumnsType<WorkProgressWeekRow> = [
    {
      title: "所在周",
      key: "period",
      width: 190,
      fixed: "left",
      render: (_: unknown, row) => weekPeriodLabel(row),
    },
    ...workProgressMetricColumns<WorkProgressWeekRow>(),
  ];
  return (
    <section aria-label="周明细" style={{ marginTop: 24 }}>
      <Typography.Title level={5} style={{ marginBottom: 12 }}>
        周明细
      </Typography.Title>
      {error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={extractErrorMessage(error, "周明细加载失败")}
        />
      ) : null}
      <Table<WorkProgressWeekRow>
        rowKey="week_start"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: columnsWidth(columns) + EXPAND_COLUMN_WIDTH }}
        pagination={false}
        onRow={() => ({ style: { cursor: "pointer" } })}
        expandable={{
          // 整行可点（鼠标）；键盘走展开按钮（原生按钮，Tab 到它，Enter / 空格展开）
          expandRowByClick: true,
          expandedRowRender: (row) => {
            const target = weekDrillTarget(row, season);
            return <DrillPromotionTable key={drillTargetKey(target)} target={target} />;
          },
          expandIcon: ({ expanded, onExpand, record }) => (
            <Button
              type="text"
              size="small"
              icon={expanded ? <DownOutlined /> : <RightOutlined />}
              aria-expanded={expanded}
              aria-label={`${expanded ? "收起" : "展开"} ${weekPeriodLabel(record)} 的推广单`}
              onClick={(e) => {
                // 不冒泡到行，免得 expandRowByClick 再切一次
                e.stopPropagation();
                onExpand(record, e);
              }}
            />
          ),
        }}
      />
    </section>
  );
}
