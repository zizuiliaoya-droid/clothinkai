import { useState, type CSSProperties } from "react";
import { Button, Card, Input, Result, Segmented, Table, Tag, Typography, message, theme } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { DisplayNameCell } from "@/components/DisplayNameCell/DisplayNameCell";
import { FlowAction } from "@/features/flow/FlowAction";
import { shipStatusStyle } from "@/features/flow/stageStyle";
import { itemSpecLines } from "@/features/promotion/shipDisplay";
import { exportWarehouseShipments, listWarehouseShipments } from "@/features/warehouse/api";
import {
  WAREHOUSE_QUERY_KEY,
  WaybillFillModal,
} from "@/features/warehouse/components/WaybillFillModal";
import {
  exportErrorMessage,
  exportFilename,
  fillActionLabel,
  isForbiddenError,
} from "@/features/warehouse/shipmentForm";
import {
  WAREHOUSE_BUCKETS,
  type WarehouseBucket,
  type WarehouseShipmentRow,
} from "@/features/warehouse/types";

const ONE_LINE: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

const fmtTime = (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : null);

/**
 * 仓库发货（流程线设计 8.5）：只看推送过的推广单（待打单 / 已发货），数据走仓库专用接口
 * `/api/warehouse/shipments`——仓库 060 起没有 promotion:read，拿不到整张推广单。
 * 回填按钮按行 `ui.actions.ship_fill`，导出按页级 `ui.actions.export`；不放时间线、不做批量回填。
 */
export function WarehousePage() {
  const { token } = theme.useToken();
  const [bucket, setBucket] = useState<WarehouseBucket>("待打单");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [target, setTarget] = useState<WarehouseShipmentRow | null>(null);
  const [exporting, setExporting] = useState(false);

  const kw = keyword.trim() || undefined;
  const { data, isLoading, error } = useQuery({
    queryKey: [...WAREHOUSE_QUERY_KEY, bucket, kw, page, pageSize],
    queryFn: () => listWarehouseShipments({ bucket, keyword: kw, page, page_size: pageSize }),
    // 403 重试没有意义，直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
  });

  const rows = data?.items ?? [];
  const canExport = Boolean(data?.ui.actions?.export) && bucket === "待打单";
  const secondary: CSSProperties = { color: token.colorTextSecondary, fontSize: token.fontSizeSM };
  const dash = <span style={{ color: token.colorTextSecondary }}>—</span>;

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await exportWarehouseShipments({ bucket: "待打单", keyword: kw });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportFilename(dayjs());
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (err) {
      message.error(await exportErrorMessage(err));
    } finally {
      setExporting(false);
    }
  }

  const columns: ColumnsType<WarehouseShipmentRow> = [
    { title: "内部编码", dataIndex: "internal_code", width: 140, fixed: "left" },
    { title: "货号", dataIndex: "style_code", width: 110 },
    {
      // 品名 = 商品简称，没填回落建单快照（7a-8）；悬停看全称
      title: "品名",
      dataIndex: "display_short_name",
      width: 140,
      ellipsis: { showTitle: false },
      render: (v: string, r) => <DisplayNameCell name={v} fullTitle={r.goods_title} />,
    },
    {
      // 套装每个成员一行「简称 · 黑色 / M」；没有明细的旧单显示录入信息原文 + 「旧」；不显示 SKU 编码（规-1）
      title: "颜色尺码",
      key: "spec",
      width: 180,
      render: (_, r) => {
        const spec = itemSpecLines(r);
        return (
          <div style={{ minWidth: 0 }}>
            {spec
              ? spec.lines.map((line, i) => (
                  <div key={i} title={line} style={ONE_LINE}>
                    {line}
                    {spec.legacy && i === 0 && <Tag style={{ marginInlineStart: 4 }}>旧</Tag>}
                  </div>
                ))
              : dash}
            {r.items_updated_after_push && <Tag color="orange">明细已更新</Tag>}
          </div>
        );
      },
    },
    { title: "收件人", dataIndex: "receiver_name", width: 100, render: (v: string | null) => v || dash },
    { title: "电话", dataIndex: "receiver_phone", width: 130, render: (v: string | null) => v || dash },
    {
      title: "地址",
      dataIndex: "receiver_address",
      width: 240,
      render: (v: string | null, r) => (
        <div>
          {v ? <span>{v}</span> : dash}
          {r.receiver_updated_after_push && (
            <Tag color="orange" style={{ marginInlineStart: 4 }}>
              地址已更新
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: "推送时间",
      dataIndex: "ship_pushed_at",
      width: 140,
      render: (v: string | null, r) => (
        <div>
          <div>{fmtTime(v) ?? dash}</div>
          {r.ship_pushed_by_name && <div style={secondary}>{r.ship_pushed_by_name}</div>}
        </div>
      ),
    },
    { title: "快递公司", dataIndex: "ship_courier", width: 90, render: (v: string | null) => v || dash },
    { title: "单号", dataIndex: "ship_waybill", width: 160, render: (v: string | null) => v || dash },
    {
      title: "发货时间",
      dataIndex: "shipped_at",
      width: 140,
      render: (v: string | null) => fmtTime(v) ?? dash,
    },
    {
      title: "状态",
      dataIndex: "ship_status",
      width: 90,
      render: (v: string) => {
        const s = shipStatusStyle(v);
        return <Tag color={s.color}>{s.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "op",
      width: 90,
      fixed: "right",
      render: (_, r) => (
        <FlowAction
          ui={r.ui}
          actionKey="ship_fill"
          label={fillActionLabel(r.ship_status)}
          onClick={() => setTarget(r)}
          buttonProps={{ type: "link", size: "small" }}
        />
      ),
    },
  ];

  const title = (
    <Typography.Title level={4} style={{ margin: 0 }}>
      仓库发货
    </Typography.Title>
  );

  // 菜单只对仓库 / 管理员显示；直接输入 URL 进来、接口 403 时给明确提示，不显示空表
  if (isForbiddenError(error)) {
    return (
      <Card title={title}>
        <Result
          status="403"
          title="没有权限查看仓库发货"
          subTitle="仓库发货只对仓库和管理员开放；需要查看请联系管理员开通权限。"
        />
      </Card>
    );
  }

  return (
    <Card
      title={title}
      extra={
        canExport ? (
          <Button
            icon={<DownloadOutlined />}
            loading={exporting}
            disabled={(data?.total ?? 0) === 0}
            onClick={() => void handleExport()}
          >
            导出待打单 Excel
          </Button>
        ) : null
      }
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 16 }}>
        <Segmented<WarehouseBucket>
          value={bucket}
          onChange={(v) => {
            setBucket(v);
            setPage(1);
          }}
          options={[...WAREHOUSE_BUCKETS]}
        />
        <Input.Search
          aria-label="搜索发货单"
          placeholder="编码 / 品名 / 收件人 / 单号"
          allowClear
          style={{ width: 300, maxWidth: "100%" }}
          onSearch={(v) => {
            setKeyword(v);
            setPage(1);
          }}
        />
      </div>
      <Table<WarehouseShipmentRow>
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1750 }}
        pagination={{
          current: data?.page ?? page,
          pageSize: data?.page_size ?? pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />

      <WaybillFillModal target={target} couriers={data?.couriers ?? []} onClose={() => setTarget(null)} />
    </Card>
  );
}
