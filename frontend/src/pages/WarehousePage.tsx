import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Segmented,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listPromotions, updateWarehouseWaybill } from "@/features/promotion/api";
import type { Promotion } from "@/features/promotion/types";
import { extractErrorMessage } from "@/services/apiClient";

type Bucket = "待打单" | "已打单" | "全部";

function se(p: Promotion, k: string): string {
  const v = (p.source_extra ?? {})[k];
  return v == null ? "" : String(v);
}

/**
 * 仓库打单：集中展示"已填打单地址、待仓库打单"的站外推广单，
 * 仓库打完单后回传发货单号（写入 source_extra['发货单号']）→ 视为已打单。
 * 避免逐条翻找。数据源复用站外推广 + source_extra，无需额外建单。
 */
export function WarehousePage() {
  const qc = useQueryClient();
  const [bucket, setBucket] = useState<Bucket>("待打单");
  const [target, setTarget] = useState<Promotion | null>(null);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ["promotions", "warehouse"],
    queryFn: () => listPromotions({ page: 1, page_size: 100, is_active: true }),
  });

  const rows = useMemo(() => {
    const all = data?.items ?? [];
    const withAddr = all.filter((p) => se(p, "打单地址").trim() !== "");
    if (bucket === "全部") return withAddr;
    if (bucket === "已打单")
      return withAddr.filter((p) => se(p, "发货单号").trim() !== "");
    return withAddr.filter((p) => se(p, "发货单号").trim() === "");
  }, [data, bucket]);

  const saveMutation = useMutation({
    mutationFn: ({ id, waybill }: { id: string; waybill: string }) =>
      updateWarehouseWaybill(id, waybill),
    onSuccess: () => {
      message.success("发货单号已回传，标记为已打单");
      setTarget(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openFill(p: Promotion) {
    setTarget(p);
    form.resetFields();
    form.setFieldsValue({ 发货单号: se(p, "发货单号") });
  }

  const columns: ColumnsType<Promotion> = [
    { title: "内部编码", dataIndex: "internal_code", width: 150 },
    { title: "货号", dataIndex: "style_code_snapshot", width: 110 },
    { title: "品名", dataIndex: "style_short_name_snapshot", width: 130, render: (v) => v || "—" },
    { title: "颜色及规格", key: "cs", width: 120, render: (_, r) => se(r, "颜色及规格") || "—" },
    { title: "打单地址", key: "addr", width: 240, render: (_, r) => se(r, "打单地址") || "—" },
    {
      title: "发货单号",
      key: "waybill",
      width: 160,
      render: (_, r) => se(r, "发货单号") || "—",
    },
    {
      title: "打单状态",
      key: "status",
      width: 100,
      render: (_, r) =>
        se(r, "发货单号").trim() !== "" ? (
          <Tag color="green">已打单</Tag>
        ) : (
          <Tag color="orange">待打单</Tag>
        ),
    },
    {
      title: "操作",
      width: 120,
      fixed: "right",
      render: (_, r) => (
        <Button type="link" size="small" onClick={() => openFill(r)}>
          {se(r, "发货单号").trim() !== "" ? "修改单号" : "回传单号"}
        </Button>
      ),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          仓库打单
        </Typography.Title>
      }
      extra={
        <Segmented
          value={bucket}
          onChange={(v) => setBucket(v as Bucket)}
          options={["待打单", "已打单", "全部"]}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1100 }}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      />

      <Modal
        title="回传发货单号"
        open={!!target}
        onCancel={() => setTarget(null)}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
      >
        <div style={{ marginBottom: 12, color: "#475569" }}>
          打单地址：{target ? se(target, "打单地址") || "—" : "—"}
        </div>
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) =>
            target &&
            saveMutation.mutate({ id: target.id, waybill: String(v.发货单号).trim() })
          }
        >
          <Form.Item
            name="发货单号"
            label="发货单号"
            rules={[{ required: true, message: "请输入发货单号" }]}
          >
            <Input placeholder="仓库打单回传的快递单号" allowClear />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
