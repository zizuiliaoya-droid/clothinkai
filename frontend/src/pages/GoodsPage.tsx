import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createGoods,
  deleteGoods,
  listBrands,
  listDictItems,
  listGoods,
  listStyles,
  updateGoods,
  type Goods,
  type GoodsFilters,
  type GoodsStyleItemInput,
} from "@/features/product/api";
import { extractErrorMessage } from "@/services/apiClient";

interface FormValues {
  goods_code: string;
  goods_title: string;
  category?: string;
  season?: string;
  brand_id?: string;
  remark?: string;
  is_active?: boolean;
  items: { style_id: string; single_goods_cost?: number | null }[];
}

/** 把表单里的数字成本转成后端要的字符串，空值保持 null 让后端回落到 SKU 成本价。 */
function toCost(v: number | null | undefined): string | null {
  return v === null || v === undefined ? null : String(v);
}

function fmtCost(v: string | null): string {
  return v === null ? "—" : `¥${Number(v).toFixed(2)}`;
}

/**
 * 商品 / 套装管理。
 *
 * 「商品」是店铺里卖的一个东西，也是报表归属的主体：投产报表按商品聚合，平台链接与推广
 * 记录都归到商品上。款式（货品）是另一层 —— 商品引用款式，套装就是引用了多个款式的商品。
 *
 * 单品与套装不分两个页面：它们的区别只有成员数量，``is_suit`` 由后端按成员数推导，
 * 列表里用标签区分即可。
 */
export function GoodsPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<GoodsFilters>({
    page: 1,
    page_size: 20,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Goods | null>(null);
  const [styleKeyword, setStyleKeyword] = useState("");
  const [form] = Form.useForm<FormValues>();

  const { data, isLoading } = useQuery({
    queryKey: ["goods", filters],
    queryFn: () => listGoods(filters),
  });
  const { data: brands } = useQuery({
    queryKey: ["brands", "options"],
    queryFn: () => listBrands({ page: 1, page_size: 100, is_active: true }),
  });
  const { data: categories } = useQuery({
    queryKey: ["dict-items", "category"],
    queryFn: () => listDictItems("category"),
  });
  const { data: seasons } = useQuery({
    queryKey: ["dict-items", "season"],
    queryFn: () => listDictItems("season"),
  });

  // 款式可能上百个，走服务端关键词搜索而不是全量拉下来。
  const { data: stylePickerData, isFetching: stylesFetching } = useQuery({
    queryKey: ["styles", "goods-picker", styleKeyword],
    queryFn: () =>
      listStyles({ page: 1, page_size: 20, keyword: styleKeyword || undefined }),
    enabled: open,
  });
  const styleOptions = useMemo(() => {
    const fromSearch = (stylePickerData?.items ?? []).map((s) => ({
      label: `${s.style_code} ${s.style_name}`,
      value: s.id,
    }));
    // 编辑时已选的成员款式可能不在当前搜索结果里，补进选项否则 Select 只显示 UUID。
    const existing = (editing?.items ?? []).map((i) => ({
      label: `${i.style_code ?? ""} ${i.style_name ?? ""}`.trim() || i.style_id,
      value: i.style_id,
    }));
    const seen = new Set(fromSearch.map((o) => o.value));
    return [...fromSearch, ...existing.filter((o) => !seen.has(o.value))];
  }, [stylePickerData, editing]);

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const items: GoodsStyleItemInput[] = (values.items ?? []).map((it, idx) => ({
        style_id: it.style_id,
        single_goods_cost: toCost(it.single_goods_cost),
        sort_order: idx,
      }));
      if (editing) {
        return updateGoods(editing.id, {
          goods_title: values.goods_title,
          category: values.category ?? null,
          season: values.season ?? null,
          brand_id: values.brand_id ?? null,
          remark: values.remark ?? null,
          is_active: values.is_active,
          items,
        });
      }
      return createGoods({
        goods_code: values.goods_code,
        goods_title: values.goods_title,
        category: values.category ?? null,
        season: values.season ?? null,
        brand_id: values.brand_id ?? null,
        remark: values.remark ?? null,
        items,
      });
    },
    onSuccess: (saved) => {
      message.success(
        saved.is_suit ? "已保存（含多个款式，记为套装）" : "已保存"
      );
      closeModal();
      void qc.invalidateQueries({ queryKey: ["goods"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteGoods(id),
    onSuccess: () => {
      message.success("商品已删除");
      void qc.invalidateQueries({ queryKey: ["goods"] });
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
    form.setFieldsValue({ items: [{ style_id: "" }] as FormValues["items"] });
    setOpen(true);
  }

  function openEdit(record: Goods) {
    setEditing(record);
    form.resetFields();
    form.setFieldsValue({
      goods_code: record.goods_code,
      goods_title: record.goods_title,
      category: record.category ?? undefined,
      season: record.season ?? undefined,
      brand_id: record.brand_id ?? undefined,
      remark: record.remark ?? undefined,
      is_active: record.is_active,
      items: record.items.map((i) => ({
        style_id: i.style_id,
        single_goods_cost:
          i.single_goods_cost === null ? null : Number(i.single_goods_cost),
      })),
    });
    setOpen(true);
  }

  const columns: ColumnsType<Goods> = [
    {
      title: "商品编码",
      dataIndex: "goods_code",
      width: 170,
      fixed: "left",
      render: (v: string) => <Typography.Text copyable>{v}</Typography.Text>,
    },
    {
      title: "商品名称",
      dataIndex: "goods_title",
      width: 260,
      ellipsis: true,
      render: (v: string, row) => (
        <Space size={4}>
          <span>{v}</span>
          {row.is_suit && <Tag color="purple">套装</Tag>}
        </Space>
      ),
    },
    {
      title: "成员款式",
      dataIndex: "items",
      width: 240,
      render: (_, row) =>
        row.items.length === 0 ? (
          "—"
        ) : (
          <Tooltip
            title={row.items
              .map((i) => `${i.style_code ?? ""} ${i.style_name ?? ""}`.trim())
              .join("、")}
          >
            <span>
              {row.items.map((i) => i.style_code ?? "?").join(" + ")}
            </span>
          </Tooltip>
        ),
    },
    {
      title: "成本",
      dataIndex: "total_cost",
      width: 130,
      align: "right",
      render: (v: string | null, row) => (
        <Space size={4}>
          <span>{fmtCost(v)}</span>
          {row.cost_missing_count > 0 && (
            <Tooltip
              title={`${row.cost_missing_count} 个款式还没有成本价，这个合计是不完整的`}
            >
              <Tag color="orange">缺{row.cost_missing_count}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: "链接",
      dataIndex: "link_count",
      width: 90,
      align: "center",
      render: (n: number) =>
        n === 0 ? (
          <Tooltip title="还没挂平台链接，这个商品不会出现在任何销售数据里">
            <Tag color="red">未上架</Tag>
          </Tooltip>
        ) : (
          <Tag color="blue">{n} 条</Tag>
        ),
    },
    { title: "类目", dataIndex: "category", width: 110, render: (v) => v || "—" },
    { title: "季节", dataIndex: "season", width: 100, render: (v) => v || "—" },
    {
      title: "品牌",
      dataIndex: "brand_name",
      width: 130,
      render: (v: string | null) => v || "—",
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
      width: 120,
      fixed: "right",
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm
            title="删除这个商品？"
            description="历史推广记录会保留归属；仍挂着平台链接的商品删不掉，要先改链接归属。"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          商品 / 套装
        </Typography.Title>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建商品
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        商品是报表归属的主体：投产报表按商品聚合，平台链接与推广费都归到商品上。
        引用了多个款式的商品自动记为套装，成本按成员相加。
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="商品编码 / 商品名 / 成员货号 / 款名"
          allowClear
          style={{ width: 280 }}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="类型"
          allowClear
          style={{ width: 110 }}
          options={[
            { label: "单品", value: "single" },
            { label: "套装", value: "suit" },
          ]}
          onChange={(v) =>
            setFilters((f) => ({
              ...f,
              is_suit: v === undefined ? undefined : v === "suit",
              page: 1,
            }))
          }
        />
        <Select
          placeholder="类目"
          allowClear
          style={{ width: 130 }}
          options={(categories ?? []).map((c) => ({
            label: c.value,
            value: c.value,
          }))}
          onChange={(v) => setFilters((f) => ({ ...f, category: v, page: 1 }))}
        />
        <Select
          placeholder="季节"
          allowClear
          style={{ width: 120 }}
          options={(seasons ?? []).map((s) => ({
            label: s.value,
            value: s.value,
          }))}
          onChange={(v) => setFilters((f) => ({ ...f, season: v, page: 1 }))}
        />
        <Space size={4}>
          <Switch
            checked={filters.unlinked_only ?? false}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                unlinked_only: v || undefined,
                page: 1,
              }))
            }
          />
          <Tooltip title="这些商品还没挂平台链接，不会出现在任何销售数据里">
            <span>只看未上架</span>
          </Tooltip>
        </Space>
        <Space size={4}>
          <Switch
            checked={filters.include_inactive ?? false}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                include_inactive: v || undefined,
                page: 1,
              }))
            }
          />
          <span>含停用</span>
        </Space>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1600 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <Modal
        title={editing ? `编辑商品 · ${editing.goods_code}` : "新建商品"}
        open={open}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={720}
      >
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v) => saveMutation.mutate(v)}
        >
          <Space align="start" style={{ display: "flex" }}>
            <Form.Item
              name="goods_code"
              label="商品编码"
              rules={[{ required: true, message: "请填商品编码" }]}
              tooltip={
                editing
                  ? "建档后不可改：报表、平台链接与推广记录都按它引用商品"
                  : "租户内唯一，建档后不可改。套装可用 SUIT- 前缀便于识别。"
              }
              style={{ width: 240 }}
            >
              <Input placeholder="如 260419 或 SUIT-1074568657697" disabled={!!editing} />
            </Form.Item>
            <Form.Item
              name="goods_title"
              label="商品名称"
              rules={[{ required: true, message: "请填商品名称" }]}
              style={{ width: 400 }}
            >
              <Input placeholder="店铺里显示的名字" />
            </Form.Item>
          </Space>

          <Space align="start" style={{ display: "flex" }}>
            <Form.Item name="category" label="类目" style={{ width: 200 }}>
              <Select
                allowClear
                placeholder="选择类目"
                options={(categories ?? []).map((c) => ({
                  label: c.value,
                  value: c.value,
                }))}
              />
            </Form.Item>
            <Form.Item name="season" label="季节" style={{ width: 200 }}>
              <Select
                allowClear
                placeholder="选择季节"
                options={(seasons ?? []).map((s) => ({
                  label: s.value,
                  value: s.value,
                }))}
              />
            </Form.Item>
            <Form.Item name="brand_id" label="品牌" style={{ width: 240 }}>
              <Select
                allowClear
                placeholder="选择品牌"
                options={(brands?.items ?? []).map((b) => ({
                  label: b.brand_name,
                  value: b.id,
                }))}
              />
            </Form.Item>
          </Space>

          <Typography.Text strong>成员款式</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            挂 1 个款式是单品，挂 2 个以上自动记为套装。成本留空则取该款式 SKU 的成本价。
          </Typography.Paragraph>
          <Form.List
            name="items"
            rules={[
              {
                validator: async (_, items) => {
                  if (!items || items.length === 0) {
                    throw new Error("至少要挂 1 个款式");
                  }
                },
              },
            ]}
          >
            {(fields, { add, remove }, { errors }) => (
              <>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: "flex" }}>
                    <Form.Item
                      name={[field.name, "style_id"]}
                      rules={[{ required: true, message: "请选款式" }]}
                      style={{ width: 380 }}
                    >
                      <Select
                        showSearch
                        filterOption={false}
                        placeholder="搜货号或款名"
                        loading={stylesFetching}
                        onSearch={setStyleKeyword}
                        options={styleOptions}
                      />
                    </Form.Item>
                    <Form.Item name={[field.name, "single_goods_cost"]} style={{ width: 160 }}>
                      <InputNumber
                        placeholder="单件成本"
                        min={0}
                        precision={2}
                        style={{ width: "100%" }}
                        addonBefore="¥"
                      />
                    </Form.Item>
                    {fields.length > 1 && (
                      <MinusCircleOutlined
                        onClick={() => remove(field.name)}
                        style={{ cursor: "pointer" }}
                      />
                    )}
                  </Space>
                ))}
                <Form.Item>
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    onClick={() => add({ style_id: "" })}
                    block
                  >
                    加一个款式
                  </Button>
                  <Form.ErrorList errors={errors} />
                </Form.Item>
              </>
            )}
          </Form.List>

          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
          {editing && (
            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
