// 报表下钻弹窗（7e R1，报表视图设计 2.1 / 2.5）：工作进度表点 PR 行 → 该 PR 在区间内的推广单；
// R5 起也按商品 / 季节 / 周取单，表格单独导出给周明细的行展开用。
// 字段是后端白名单；金额读不到显示「—」；只按后端详情可见性提供链接。
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Modal, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getDrillPromotions } from "@/features/report/api";
import {
  DRILL_PAGE_SIZE,
  drillPromotionParams,
  drillTargetKey,
  type DrillPromotionTarget,
} from "@/features/report/drill";
import type { DrillPromotionRow } from "@/features/report/types";
import { extractErrorMessage } from "@/services/apiClient";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const text = (v: string | null) => v ?? "—";

const COLUMNS: ColumnsType<DrillPromotionRow> = [
  { title: "推广编号", dataIndex: "internal_code", width: 130, fixed: "left",
    render: (value: string, row) => row.detail_url ? <Link to={row.detail_url}>{value}</Link> : value },
  { title: "合作日期", dataIndex: "cooperation_date", width: 110 },
  { title: "约定发布", dataIndex: "scheduled_publish_date", width: 110, render: text },
  { title: "发布日期", dataIndex: "actual_publish_date", width: 110, render: text },
  { title: "博主", dataIndex: "blogger_nickname", width: 140, ellipsis: true },
  { title: "平台", dataIndex: "platform", width: 90 },
  { title: "商品", dataIndex: "display_short_name", width: 140, ellipsis: true },
  { title: "发布状态", dataIndex: "publish_status", width: 90 },
  { title: "召回状态", dataIndex: "recall_status", width: 100 },
  { title: "服务费", dataIndex: "quote_amount", width: 100, align: "right", render: money },
  {
    title: "站外推广成本",
    dataIndex: "total_promo_cost",
    width: 120,
    align: "right",
    render: money,
  },
  {
    title: "7天点赞",
    dataIndex: "like_count",
    width: 90,
    align: "right",
    render: (v: number | null) => (v == null ? "—" : v),
  },
];

const SCROLL_X = COLUMNS.reduce((sum, c) => sum + (Number(c.width) || 0), 0);

export function DrillPromotionTable({ target }: { target: DrillPromotionTarget }) {
  const [page, setPage] = useState(1);
  const { data, isFetching, error } = useQuery({
    queryKey: ["report-drill-promotions", drillTargetKey(target), page],
    queryFn: () => getDrillPromotions(drillPromotionParams(target, page)),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <Typography.Paragraph style={{ color: "#475569", marginBottom: 12 }}>
        合作日期 {target.dateFrom} 至 {target.dateTo}
      </Typography.Paragraph>
      {error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={extractErrorMessage(error, "推广单加载失败")}
        />
      ) : null}
      <Table<DrillPromotionRow>
        rowKey="id"
        size="small"
        loading={isFetching}
        columns={COLUMNS}
        dataSource={data?.items ?? []}
        scroll={{ x: SCROLL_X }}
        pagination={{
          current: page,
          pageSize: DRILL_PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 条`,
          onChange: setPage,
        }}
      />
    </>
  );
}

interface Props {
  /** null = 关闭。 */
  target: DrillPromotionTarget | null;
  onClose: () => void;
}

export function DrillPromotionModal({ target, onClose }: Props) {
  return (
    <Modal
      open={target != null}
      title={target ? `${target.title} 的推广单` : undefined}
      onCancel={onClose}
      footer={null}
      width={1100}
      destroyOnHidden
    >
      {target ? (
        // 换对象时整块重建，页码回到第一页
        <DrillPromotionTable
          key={drillTargetKey(target)}
          target={target}
        />
      ) : null}
    </Modal>
  );
}
