import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createSku,
  deleteSku,
  listBrands,
  listCostTable,
  listDictItems,
  listStyles,
  updateSku,
} from "@/features/product/api";
import type {
  CostTableFilters,
  CostTableRow,
  SkuCreate,
  SourcingType,
} from "@/features/product/types";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

const SOURCING_TYPES: SourcingType[] = ["自产", "外采", "混合"];

/** BR-U02-13：自产必须有成本价，外采必须有采购价，混合两者都可空。 */
function requiredPriceField(sourcing: SourcingType | undefined) {
  if (sourcing === "自产") return "cost_price" as const;
  if (sourcing === "外采") return "purchase_price" as const;
  return null;
}

interface FormValues {
  style_id: string;
  sku_code: string;
  color: string;
  size: string;
  base_price?: number | null;
  cost_price?: number | null;
  purchase_price?: number | null;
  tag_price?: number | null;
  sourcing_type: SourcingType;
}

/** 数值 → 后端期望的字符串金额；空值保持 null 以便清空。 */
const toAmount = (v: number | null | undefined) =>
  v == null ? null : String(v);

/** 金额是否等值。后端回 "100.00"、表单回 100，按字符串比会误判成「改过」。 */
function sameAmount(a: string | null, b: string | null): boolean {
  if (a == null || b == null) return a == null && b == null;
  return Number(a) === Number(b);
}

const PRICE_FIELDS = [
  "base_price",
  "cost_price",
  "purchase_price",
  "tag_price",
] as const;

type PriceField = (typeof PRICE_FIELDS)[number];

/**
 * 只回传真正改动过的价格字段。
 *
 * 后端的价格写权限检查看的是 payload 里出现了哪些字段（``model_fields_set``），
 * 而无价格权限的用户读到的 cost_price 本身就是 null。无条件回传会让他仅改颜色
 * 也被判成越权写价格。
 */
function changedPrices(
  row: CostTableRow,
  values: FormValues,
): Partial<Record<PriceField, string | null>> {
  const out: Partial<Record<PriceField, string | null>> = {};
  for (const field of PRICE_FIELDS) {
    const next = toAmount(values[field]);
    if (!sameAmount(row[field], next)) out[field] = next;
  }
  return out;
}

export function CostTablePage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<CostTableFilters>({
    page: 1,
    page_size: 20,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<CostTableRow | null>(null);
  const [styleKeyword, setStyleKeyword] = useState("");
  const [form] = Form.useForm<FormValues>();
  const sourcing = Form.useWatch("sourcing_type", form);

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

  const { data: colors } = useQuery({
    queryKey: ["dict-items", "color"],
    queryFn: () => listDictItems("color"),
  });
  const { data: sizes } = useQuery({
    queryKey: ["dict-items", "size"],
    queryFn: () => listDictItems("size"),
  });

  // 新增商品要先挑款式。款式可能上百个，走服务端关键词搜索而不是全量拉下来。
  const { data: styleOptionsData, isFetching: stylesFetching } = useQuery({
    queryKey: ["styles", "picker", styleKeyword],
    queryFn: () =>
      listStyles({ page: 1, page_size: 20, keyword: styleKeyword || undefined }),
    enabled: open && !editing,
  });
  const styleOptions = useMemo(
    () =>
      (styleOptionsData?.items ?? []).map((s) => ({
        label: `${s.style_code} ${s.style_name}`,
        value: s.id,
      })),
    [styleOptionsData],
  );

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      if (editing) {
        return updateSku(editing.sku_id, {
          sku_code: values.sku_code,
          color: values.color,
          size: values.size,
          sourcing_type: values.sourcing_type,
          ...changedPrices(editing, values),
        });
      }
      const payload: SkuCreate = {
        style_id: values.style_id,
        sku_code: values.sku_code,
        color: values.color,
        size: values.size,
        sourcing_type: values.sourcing_type,
        base_price: toAmount(values.base_price),
        cost_price: toAmount(values.cost_price),
        purchase_price: toAmount(values.purchase_price),
        tag_price: toAmount(values.tag_price),
      };
      return createSku(payload);
    },
    onSuccess: () => {
      message.success(editing ? "商品已更新" : "商品已新增");
      closeModal();
      void qc.invalidateQueries({ queryKey: ["cost-table"] });
      void qc.invalidateQueries({ queryKey: ["skus"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (row: CostTableRow) => deleteSku(row.sku_id),
    onSuccess: () => {
      message.success("商品已删除");
      void qc.invalidateQueries({ queryKey: ["cost-table"] });
      void qc.invalidateQueries({ queryKey: ["skus"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeModal() {
    setOpen(false);
    setEditing(null);
    setStyleKeyword("");
    form.resetFields();
  }

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ sourcing_type: "自产" });
    setOpen(true);
  }

  function openEdit(row: CostTableRow) {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue({
      sku_code: row.sku_code,
      color: row.color,
      size: row.size,
      base_price: row.base_price == null ? null : Number(row.base_price),
      cost_price: row.cost_price == null ? null : Number(row.cost_price),
      purchase_price:
        row.purchase_price == null ? null : Number(row.purchase_price),
      tag_price: row.tag_price == null ? null : Number(row.tag_price),
      sourcing_type: row.sourcing_type as SourcingType,
    });
    setOpen(true);
  }

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
    { title: "货号", dataIndex: "style_code", width: 120, fixed: "left" },
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
    { title: "采购方式", dataIndex: "sourcing_type", width: 90 },
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
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 130,
      fixed: "right",
      render: (_, row) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Popconfirm
            title="删除该商品？"
            description="已被推广或订单引用的商品无法删除，只能停用。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => deleteMutation.mutate(row)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const requiredPrice = requiredPriceField(sourcing);

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          商品成本表
        </Typography.Title>
      }
      extra={
        <Space>
          <ImportUploadButton
            source="manual_style_sku"
            label="导入商品成本表"
            invalidateKeys={[["cost-table"], ["styles"], ["skus"]]}
            templateColumns={[
              "货号", "商品编码", "商品名称", "商品简称", "颜色及规格",
              "颜色", "规格", "基本售价", "成本价", "采购价", "市场吊牌价", "品牌",
            ]}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增商品
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索货号 / 商品编码 / 名称"
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
        <Select
          value={filters.include_inactive ? "all" : "active"}
          style={{ width: 120 }}
          options={[
            { label: "仅启用", value: "active" },
            { label: "全部", value: "all" },
          ]}
          onChange={(v) =>
            setFilters((f) => ({
              ...f,
              include_inactive: v === "all" ? true : undefined,
              page: 1,
            }))
          }
        />
      </Space>

      <Table
        rowKey="sku_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1700 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <Modal
        title={editing ? "编辑商品" : "新增商品"}
        open={open}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => saveMutation.mutate(v)}
          style={{ marginTop: 16 }}
        >
          {editing ? (
            <Form.Item label="所属款式">
              <Input
                value={`${editing.style_code} ${editing.style_name}`}
                disabled
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                款名、简称、品牌属于款式，请到「款式管理」修改。
              </Typography.Text>
            </Form.Item>
          ) : (
            <Form.Item
              name="style_id"
              label="所属款式"
              rules={[{ required: true, message: "请选择款式" }]}
              extra="按货号或款名搜索。若款式还不存在，请先到「款式管理」新建。"
            >
              <Select
                showSearch
                filterOption={false}
                placeholder="搜索货号 / 款名"
                loading={stylesFetching}
                options={styleOptions}
                onSearch={setStyleKeyword}
                notFoundContent={stylesFetching ? "搜索中…" : "无匹配款式"}
              />
            </Form.Item>
          )}
          <Form.Item
            name="sku_code"
            label="商品编码"
            rules={[
              { required: true, message: "请输入商品编码" },
              {
                pattern: /^[A-Za-z0-9_-]+$/,
                message: "仅支持字母、数字、下划线与连字符",
              },
            ]}
          >
            <Input placeholder="如 A001-RED-M" />
          </Form.Item>
          <Space size="large" align="start">
            <Form.Item
              name="color"
              label="颜色"
              rules={[{ required: true, message: "请选择颜色" }]}
            >
              <Select
                style={{ width: 200 }}
                placeholder="选择颜色"
                options={(colors ?? []).map((c) => ({
                  label: c.value,
                  value: c.value,
                }))}
                notFoundContent="暂无颜色，请先在款式管理的「管理字典」中添加"
              />
            </Form.Item>
            <Form.Item
              name="size"
              label="规格 / 尺码"
              rules={[{ required: true, message: "请选择规格" }]}
            >
              <Select
                style={{ width: 200 }}
                placeholder="选择规格"
                options={(sizes ?? []).map((s) => ({
                  label: s.value,
                  value: s.value,
                }))}
                notFoundContent="暂无规格，请先在款式管理的「管理字典」中添加"
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="sourcing_type"
            label="采购方式"
            rules={[{ required: true, message: "请选择采购方式" }]}
            extra="自产必须填成本价，外采必须填采购价，混合两者都可留空。"
          >
            <Select
              style={{ width: 200 }}
              options={SOURCING_TYPES.map((t) => ({ label: t, value: t }))}
            />
          </Form.Item>
          <Space size="large" align="start" wrap>
            <Form.Item
              name="cost_price"
              label="成本价"
              rules={[
                {
                  required: requiredPrice === "cost_price",
                  message: "自产商品必须填写成本价",
                },
              ]}
            >
              <InputNumber
                style={{ width: 160 }}
                min={0}
                precision={2}
                prefix="¥"
                placeholder="成本价"
              />
            </Form.Item>
            <Form.Item
              name="purchase_price"
              label="采购价"
              rules={[
                {
                  required: requiredPrice === "purchase_price",
                  message: "外采商品必须填写采购价",
                },
              ]}
            >
              <InputNumber
                style={{ width: 160 }}
                min={0}
                precision={2}
                prefix="¥"
                placeholder="采购价"
              />
            </Form.Item>
          </Space>
          <Space size="large" align="start" wrap>
            <Form.Item name="base_price" label="基本售价">
              <InputNumber
                style={{ width: 160 }}
                min={0}
                precision={2}
                prefix="¥"
                placeholder="基本售价"
              />
            </Form.Item>
            <Form.Item name="tag_price" label="市场 / 吊牌价">
              <InputNumber
                style={{ width: 160 }}
                min={0}
                precision={2}
                prefix="¥"
                placeholder="吊牌价"
              />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Card>
  );
}
