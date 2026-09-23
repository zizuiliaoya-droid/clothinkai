import { useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Image,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  DeleteOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import {
  listOrderAdjustments,
  removeOrderPaymentQr,
  uploadOrderPaymentQr,
} from "@/features/finance/api";
import type {
  OrderAdjustment,
  OrderAdjustmentFilters,
} from "@/features/finance/types";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";

type TypeBucket = "全部" | "拍单" | "刷单";

// 拍单/刷单导入：按 order_type 路由到不同 adapter source
const IMPORT_SOURCE: Record<"拍单" | "刷单", string> = {
  拍单: "manual_tao_order",
  刷单: "manual_brush_order",
};
const IMPORT_COLUMNS_TAO = ["日期", "订单号", "博主ID", "货号", "金额", "备注"];
const IMPORT_COLUMNS_BRUSH = [...IMPORT_COLUMNS_TAO, "是否剔除ROI"];

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

/**
 * 拍单 / 刷单合并页（后端本来就是同一张 order_adjustment 表，order_type 区分）。
 *
 * 列对齐 final.xlsx「拍单」：销售类型|拍单日期|订单号|博主ID/微信ID|款式|款号|金额|付款金额|付款日期，
 * 刷单额外展示「ROI剔除」。每行可上传博主收款码（私有桶，列表里给短时签名 URL）。
 */
export function OrderAdjustmentPage() {
  const qc = useQueryClient();
  const [bucket, setBucket] = useState<TypeBucket>("全部");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [keyword, setKeyword] = useState<string>();
  const [statusFilter, setStatusFilter] = useState<string>();
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [amountMin, setAmountMin] = useState<number | null>(null);
  const [amountMax, setAmountMax] = useState<number | null>(null);
  const [uploadingId, setUploadingId] = useState<string | null>(null);

  const filters: OrderAdjustmentFilters = {
    page,
    page_size: pageSize,
    order_type: bucket === "全部" ? undefined : bucket,
    status: statusFilter,
    keyword,
    order_date_from: dateRange?.[0]?.format("YYYY-MM-DD"),
    order_date_to: dateRange?.[1]?.format("YYYY-MM-DD"),
    amount_min: amountMin ?? undefined,
    amount_max: amountMax ?? undefined,
  };

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["order-adjustments", filters],
    queryFn: () => listOrderAdjustments(filters),
  });

  const uploadMutation = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      uploadOrderPaymentQr(id, file),
    onSuccess: () => {
      message.success("收款码已上传");
      void qc.invalidateQueries({ queryKey: ["order-adjustments"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
    onSettled: () => setUploadingId(null),
  });

  const removeQrMutation = useMutation({
    mutationFn: (id: string) => removeOrderPaymentQr(id),
    onSuccess: () => {
      message.success("收款码已移除");
      void qc.invalidateQueries({ queryKey: ["order-adjustments"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function resetFilters() {
    setBucket("全部");
    setKeyword(undefined);
    setStatusFilter(undefined);
    setDateRange(null);
    setAmountMin(null);
    setAmountMax(null);
    setPage(1);
  }

  const columns: ColumnsType<OrderAdjustment> = [
    {
      title: "销售类型",
      dataIndex: "order_type",
      width: 90,
      fixed: "left",
      render: (v: string) => (
        <Tag color={v === "刷单" ? "purple" : "blue"}>{v}</Tag>
      ),
    },
    {
      title: "日期",
      dataIndex: "order_date",
      width: 110,
      render: (v) => v || "—",
    },
    { title: "订单号", dataIndex: "order_no", width: 160, render: (v) => v || "—" },
    {
      title: "博主ID/微信ID",
      dataIndex: "blogger_identifier",
      width: 140,
      render: (v) => v || "—",
    },
    { title: "款式", dataIndex: "style_name", width: 140, render: (v) => v || "—" },
    { title: "货号", dataIndex: "style_code", width: 120, render: (v) => v || "—" },
    { title: "金额", dataIndex: "amount", width: 110, render: money },
    { title: "付款金额", dataIndex: "payment_amount", width: 110, render: money },
    {
      title: "付款日期",
      dataIndex: "payment_date",
      width: 110,
      render: (v) => v || "—",
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (v: string) => (
        <Tag color={v === "已付款" ? "green" : "orange"}>{v}</Tag>
      ),
    },
    {
      title: "ROI剔除",
      dataIndex: "exclude_from_roi",
      width: 90,
      render: (v: boolean) => (v ? <Tag color="red">剔除</Tag> : "—"),
    },
    {
      title: "收款码",
      key: "payment_qr",
      width: 170,
      render: (_, row) => (
        <Space size={4}>
          {row.payment_qr_signed_url ? (
            <Image
              width={32}
              height={32}
              src={row.payment_qr_signed_url}
              alt={`${row.order_no ?? row.id} 收款码`}
              style={{ objectFit: "cover", borderRadius: 4 }}
            />
          ) : null}
          <Upload
            accept="image/jpeg,image/png,image/webp"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              setUploadingId(row.id);
              uploadMutation.mutate({ id: row.id, file });
              return false;
            }}
          >
            <Button
              type="link"
              size="small"
              icon={<UploadOutlined />}
              loading={uploadingId === row.id && uploadMutation.isPending}
            >
              {row.payment_qr_attachment_id ? "更换" : "上传"}
            </Button>
          </Upload>
          {row.payment_qr_attachment_id ? (
            <Popconfirm
              title="移除该收款码？"
              okText="移除"
              cancelText="取消"
              onConfirm={() => removeQrMutation.mutate(row.id)}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
    { title: "备注", dataIndex: "remark", render: (v) => v || "—" },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          拍单 / 刷单
          <Typography.Text
            type="secondary"
            style={{ fontSize: 13, marginLeft: 12 }}
          >
            两类单据同表管理，按销售类型切换查看
          </Typography.Text>
        </Typography.Title>
      }
      extra={
        <Space>
          <ImportUploadButton
            source={IMPORT_SOURCE["拍单"]}
            label="导入拍单"
            invalidateKeys={[["order-adjustments"]]}
            templateColumns={IMPORT_COLUMNS_TAO}
          />
          <ImportUploadButton
            source={IMPORT_SOURCE["刷单"]}
            label="导入刷单"
            invalidateKeys={[["order-adjustments"]]}
            templateColumns={IMPORT_COLUMNS_BRUSH}
          />
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Segmented
          value={bucket}
          onChange={(v) => {
            setBucket(v as TypeBucket);
            setPage(1);
          }}
          options={["全部", "拍单", "刷单"]}
        />
        <Segmented
          value={statusFilter ?? "全部状态"}
          onChange={(v) => {
            setStatusFilter(v === "全部状态" ? undefined : String(v));
            setPage(1);
          }}
          options={["全部状态", "待付款", "已付款"]}
        />
        <DatePicker.RangePicker
          value={dateRange}
          allowEmpty={[true, true]}
          placeholder={["开始日期", "结束日期"]}
          onChange={(v) => {
            setDateRange(v as [Dayjs | null, Dayjs | null] | null);
            setPage(1);
          }}
        />
        <Input.Search
          placeholder="搜索订单号 / 博主ID"
          allowClear
          style={{ width: 220 }}
          enterButton={<SearchOutlined />}
          onSearch={(v) => {
            setKeyword(v || undefined);
            setPage(1);
          }}
        />
        <Space.Compact>
          <InputNumber
            style={{ width: 118 }}
            placeholder="金额≥"
            min={0}
            precision={2}
            value={amountMin}
            onChange={(v) => {
              setAmountMin(v);
              setPage(1);
            }}
          />
          <InputNumber
            style={{ width: 118 }}
            placeholder="金额≤"
            min={0}
            precision={2}
            value={amountMax}
            onChange={(v) => {
              setAmountMax(v);
              setPage(1);
            }}
          />
        </Space.Compact>
        <Button icon={<ReloadOutlined />} onClick={resetFilters}>
          重置
        </Button>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading || isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1600 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100, 200],
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />
    </Card>
  );
}
