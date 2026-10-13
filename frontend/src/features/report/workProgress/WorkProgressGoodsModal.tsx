// 单品「查看全部」弹窗（7e R5，报表视图设计 2.5）：同一个 /work-progress/goods 接口按 20 条翻页，
// 「未归属商品」接在最后一页末尾（不可点：下钻没有「商品为空」筛选）。页面文字里不出现商品编码。
//
// 分页条单独放在表格外：最后一页多接一行，交给 Table 自带分页会被它按 pageSize 截掉。
import { useState } from "react";
import { Alert, Button, Modal, Pagination, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { extractErrorMessage } from "@/services/apiClient";
import { getWorkProgressGoods, type WorkProgressParams } from "../api";
import { HeaderHint } from "../components/HeaderHint";
import type { DrillPromotionTarget } from "../drill";
import type { WorkProgressGoodsRow } from "../types";
import { GOODS_MODAL_PAGE_SIZE, goodsDrillTarget, workProgressGoodsKey } from "./workProgressGoods";
import { columnsWidth, LIKE_HINT, money, pct, PROMO_COST_HINT } from "./workProgressColumns";

/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";

function GoodsTable({
  params,
  onDrill,
}: {
  params: WorkProgressParams;
  onDrill: (target: DrillPromotionTarget) => void;
}) {
  const [page, setPage] = useState(1);
  const offset = (page - 1) * GOODS_MODAL_PAGE_SIZE;
  const { data, isFetching, error } = useQuery({
    queryKey: workProgressGoodsKey(params, GOODS_MODAL_PAGE_SIZE, offset),
    queryFn: () =>
      getWorkProgressGoods({ ...params, limit: GOODS_MODAL_PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  });
  const total = data?.total ?? 0;
  // 按响应自己的 offset 判最后一页：翻页时 keepPreviousData 还显示上一页，不能提前接上未归属
  const onLastPage = data != null && data.offset + GOODS_MODAL_PAGE_SIZE >= data.total;
  const rows: WorkProgressGoodsRow[] = [
    ...(data?.items ?? []),
    ...(onLastPage && data.unassigned ? [data.unassigned] : []),
  ];
  const columns: ColumnsType<WorkProgressGoodsRow> = [
    {
      title: "商品",
      key: "goods",
      width: 200,
      fixed: "left",
      ellipsis: { showTitle: false },
      render: (_: unknown, row) =>
        row.goods_id && data ? (
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: "auto" }}
            aria-label={`查看 ${row.display_name} 的推广单`}
            onClick={() => onDrill(goodsDrillTarget(row, data, params.season))}
          >
            <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />
          </Button>
        ) : (
          <span style={{ color: MUTED }}>{row.display_name}</span>
        ),
    },
    { title: "约稿量", dataIndex: "quote_count", width: 80, align: "right" },
    { title: "发布量", dataIndex: "publish_count", width: 80, align: "right" },
    {
      title: "完成率",
      dataIndex: "month_complete_rate",
      width: 90,
      align: "right",
      render: pct,
    },
    { title: "超时量", dataIndex: "overdue_count", width: 80, align: "right" },
    {
      title: <HeaderHint label="点赞数" hint={LIKE_HINT} />,
      dataIndex: "like_count",
      width: 90,
      align: "right",
    },
    {
      title: <HeaderHint label="推广成本" hint={PROMO_COST_HINT} />,
      dataIndex: "promo_cost",
      width: 120,
      align: "right",
      render: money,
    },
  ];
  return (
    <>
      {data ? (
        <Typography.Paragraph style={{ color: MUTED, marginBottom: 12 }}>
          合作日期 {data.date_from} 至 {data.date_to}；没有归属商品的单汇成「未归属商品」，排在最后
        </Typography.Paragraph>
      ) : null}
      {error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={extractErrorMessage(error, "单品汇总加载失败")}
        />
      ) : null}
      <Table<WorkProgressGoodsRow>
        rowKey={(r) => r.goods_id ?? "unassigned"}
        size="small"
        loading={isFetching}
        columns={columns}
        dataSource={rows}
        scroll={{ x: columnsWidth(columns) }}
        pagination={false}
      />
      <Pagination
        style={{ marginTop: 12, justifyContent: "flex-end" }}
        current={page}
        pageSize={GOODS_MODAL_PAGE_SIZE}
        total={total}
        showSizeChanger={false}
        showTotal={(n) => `共 ${n} 个商品`}
        onChange={setPage}
      />
    </>
  );
}

interface Props {
  open: boolean;
  /** 页面当前筛选：区间 + 季节（与卡片同一口径）。 */
  params: WorkProgressParams;
  onClose: () => void;
  onDrill: (target: DrillPromotionTarget) => void;
}

export function WorkProgressGoodsModal({ open, params, onClose, onDrill }: Props) {
  return (
    <Modal
      open={open}
      title="单品汇总（全部商品）"
      onCancel={onClose}
      footer={null}
      width={900}
      destroyOnHidden
    >
      {open ? (
        // 筛选变了整块重建，页码回到第一页
        <GoodsTable key={JSON.stringify(params)} params={params} onDrill={onDrill} />
      ) : null}
    </Modal>
  );
}
