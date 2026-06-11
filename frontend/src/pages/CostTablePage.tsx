import { useState } from "react";
import { Card, Image, Input, Select, Space, Table, Tag, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listBrands, listCostTable } from "@/features/product/api";
import type { CostTableFilters, CostTableRow } from "@/features/product/types";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

export function CostTablePage() {
  const [filters, setFilters] = useState<CostTableFilters>({
    page: 1,
    page_size: 20,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["cost-table", filters],
    queryFn: () => listCostTable(filters),
  });

  const { data: brands } = useQuery({
    queryKey: ["brands", "options"],
    queryFn: () => listBrands({ page: 1, page_size: 100, is_active: true }),
  });
  const brandOptions =
    brands?.items.map((b) => ({ label: b.brand_name, value: b.id })) ?? [];

  // 列严格对齐 final.xlsx「商品成本表」：
  // 图片|款式编码|商品编码|商品名称|商品简称|颜色及规格|颜色|规格|基本售价|成本价|采购价|市场|吊牌价|品牌
  const columns: ColumnsType<CostTableRow> = [
    {
      title: "图片",
      dataIndex: "image_key",
      width: 70,
      render: (key: string | null) =>
        key ? (
          <Image width={40} height={40} src={key} fallback="" />
        ) : (
          <div
            style={{
              width: 40,
              height: 40,
              background: "#f0f0f0",
              borderRadius: 4,
            }}
          />
        ),
    },
    { title: "款式编码", dataIndex: "style_code", width: 120, fixed: "left" },
    { title: "商品编码", dataIndex: "sku_code", width: 120 },
    { title: "商品名称", dataIndex: "style_name", width: 160 },
    {
      title: "商品简称",
      dataIndex: "short_name",
      width: 130,
      render: (v) => v || "—",
    },
    { title: "颜色及规格", dataIndex: "color_size", width: 130 },
    { title: "颜色", dataIndex: "color", width: 80 },
    { title: "规格", dataIndex: "size", width: 80 },
    {
      title: "基本售价",
      dataIndex: "base_price",
      width: 100,
      render: money,
    },
    { title: "成本价", dataIndex: "cost_price", width: 100, render: money },
    {
      title: "采购价",
      dataIndex: "purchase_price",
      width: 100,
      render: money,
    },
    { title: "市场|吊牌价", dataIndex: "tag_price", width: 110, render: money },
    {
      title: "品牌",
      dataIndex: "brand_name",
      width: 100,
      render: (v) => v || "—",
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      fixed: "right",
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          商品成本表
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索款式编码 / 商品编码 / 名称"
          allowClear
          style={{ width: 260 }}
          enterButton={<SearchOutlined />}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="品牌"
          allowClear
          style={{ width: 160 }}
          options={brandOptions}
          onChange={(v) => setFilters((f) => ({ ...f, brand_id: v, page: 1 }))}
        />
      </Space>

      <Table
        rowKey="sku_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1400 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />
    </Card>
  );
}
