import { formatMoney } from "@/utils/money";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { useState } from "react";
import { Alert, Button, Input, Select, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { FlowAction } from "@/features/flow/FlowAction";
import { LineDrawer } from "@/features/flow/LineDrawer";
import { FREIGHT_PAYMENTS_KEY, listFreightPayments } from "@/features/promotion/freightApi";
import type { FreightFilters, ReturnFreightPayment } from "@/features/promotion/freightTypes";
import { FreightPaymentModal } from "./components/FreightPaymentModal";
import { useReadOnlySupport } from "@/stores/supportStore";

export function ReturnFreightTab() {
  const readOnly = useReadOnlySupport();
  const [filters, setFilters] = useState<FreightFilters>({ page: 1, page_size: 20 });
  const [line, setLine] = useState<ReturnFreightPayment | null>(null);
  const [target, setTarget] = useState<ReturnFreightPayment | null>(null);
  const [mode, setMode] = useState<"pay" | "reject">("pay");
  const { data, isLoading, error, refetch } = useQuery({ queryKey: [...FREIGHT_PAYMENTS_KEY, "list", filters], queryFn: () => listFreightPayments(filters) });
  const rows = data?.items ?? [];
  function editFilter(patch: Partial<FreightFilters>) { setFilters((previous) => ({ ...previous, ...patch, page: 1 })); }
  function open(row: ReturnFreightPayment, next: "pay" | "reject") { setTarget(row); setMode(next); }
  const columns: ColumnsType<ReturnFreightPayment> = [
    { title: "推广编号", dataIndex: "promotion_internal_code", width: 190, render: (value, row) => <Button type="link" onClick={() => setLine(row)}>{value}</Button> },
    { title: "博主", dataIndex: "blogger_nickname", width: 130 },
    { title: "商品简称", dataIndex: "display_short_name", width: 150, render: (value) => value ?? "—" },
    ...(rows.some((row) => row.amount !== null) ? [{ title: "金额", dataIndex: "amount", width: 110, render: (value: string | null) => value == null ? "—" : formatMoney(value) }] : []),
    { title: "状态", dataIndex: "status", width: 100, render: (value) => <Tag>{value}</Tag> },
    { title: "提交人 / 时间", width: 180, render: (_, row) => <>{row.submitted_by_name ?? "—"}<br />{row.submitted_at.replace("T", " ").slice(0, 16)}</> },
    ...(rows.some((row) => row.payment_qr_attachment_id || row.payment_qr_signed_url) ? [{ title: "收款码快照", width: 110, render: (_: unknown, row: ReturnFreightPayment) => row.payment_qr_signed_url ? <StyleImageThumbnail size={48} src={row.payment_qr_signed_url} alt="收款码快照" /> : row.payment_qr_attachment_id ? "暂无法预览" : "—" }] : []),
    ...(rows.some((row) => row.waybill_attachment_id || row.waybill_signed_url) ? [{ title: "寄回截图快照", width: 110, render: (_: unknown, row: ReturnFreightPayment) => row.waybill_signed_url ? <StyleImageThumbnail size={48} src={row.waybill_signed_url} alt="寄回截图快照" /> : row.waybill_attachment_id ? "暂无法预览" : "—" }] : []),
    { title: "审核人", dataIndex: "reviewed_by_name", width: 110, render: (value) => value ?? "—" },
    { title: "付款日期", dataIndex: "payment_date", width: 120, render: (value) => value ?? "—" },
    ...(rows.some((row) => row.payment_proof_attachment_id || row.payment_proof_signed_url) ? [{ title: "付款凭证", width: 100, render: (_: unknown, row: ReturnFreightPayment) => row.payment_proof_signed_url ? <StyleImageThumbnail size={48} src={row.payment_proof_signed_url} alt="寄回运费付款凭证" /> : row.payment_proof_attachment_id ? "暂无法预览" : "—" }] : []),
    ...(!readOnly && rows.some((row) => row.ui.actions?.freight_pay || row.ui.actions?.freight_reject_payment) ? [{ title: "操作", width: 160, render: (_: unknown, row: ReturnFreightPayment) => <Space wrap><FlowAction ui={row.ui} actionKey="freight_pay" label="付款" onClick={() => open(row, "pay")} showReasonBelow /><FlowAction ui={row.ui} actionKey="freight_reject_payment" label="驳回" onClick={() => open(row, "reject")} showReasonBelow /></Space> }] : []),
  ];
  return <>
    <Space wrap style={{ marginBottom: 16 }}>
      <Select aria-label="运费状态" placeholder="运费状态" allowClear style={{ width: 140 }} value={filters.status} options={["待审核", "待付款", "已付款", "已驳回"].map((value) => ({ value, label: value }))} onChange={(status) => editFilter({ status })} />
      <Input aria-label="运费关键词" placeholder="推广编号、博主" maxLength={64} style={{ width: 220 }} value={filters.keyword ?? ""} onChange={(event) => editFilter({ keyword: event.target.value || undefined })} />
      <label>付款日期 <Input type="date" aria-label="付款开始日期" style={{ width: 150 }} value={filters.payment_date_from ?? ""} onChange={(event) => editFilter({ payment_date_from: event.target.value || undefined })} /></label>
      <label>至 <Input type="date" aria-label="付款结束日期" style={{ width: 150 }} value={filters.payment_date_to ?? ""} onChange={(event) => editFilter({ payment_date_to: event.target.value || undefined })} /></label>
    </Space>
    {error ? <Alert type="error" showIcon message={(error as { response?: { status?: number } }).response?.status === 403 ? "没有查看寄回运费列表的权限" : "寄回运费加载失败"} action={<Button onClick={() => void refetch()}>重试</Button>} /> : <Table rowKey="id" size="small" loading={isLoading} columns={columns} dataSource={rows} scroll={{ x: 1300 }} locale={{ emptyText: "暂无寄回运费付款单" }} pagination={{ current: data?.page ?? filters.page, pageSize: data?.page_size ?? filters.page_size, total: data?.total ?? 0, showTotal: (total) => `共 ${total} 条`, onChange: (page, page_size) => setFilters((previous) => ({ ...previous, page, page_size })) }} />}
    <LineDrawer lineId={line?.line_id ?? null} onClose={() => setLine(null)} goodsName={line?.display_short_name} bloggerName={line?.blogger_nickname} />
    {target && <FreightPaymentModal key={`${target.id}-${mode}`} target={target} mode={mode} onClose={() => setTarget(null)} />}
  </>;
}
