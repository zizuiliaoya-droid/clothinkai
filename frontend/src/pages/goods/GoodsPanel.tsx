import { useEffect, useMemo, useState } from "react";
import {
  Button,
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
  GOODS_SHORT_NAME_MAX_LEN,
  createGoods,
  deleteGoods,
  getGoodsSeasonOptions,
  goodsDisplayName,
  listGoods,
  listStyles,
  updateGoods,
  type Goods,
  type GoodsFilters,
  type GoodsStyleItemInput,
} from "@/features/product/api";
import type { Style } from "@/features/product/types";
import { GoodsImages } from "@/components/GoodsImages/GoodsImages";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { PermissionGate } from "@/components/PermissionGate/PermissionGate";
import { extractErrorMessage } from "@/services/apiClient";
import { StyleEditModal } from "./StyleEditModal";
import { SuitLinkModal } from "./SuitLinkModal";

/** 能绑平台链接的角色，与 App.tsx 里 /platform-links 的 RoleRoute 一致（后端校验 ops.platform_link:write）。 */
const LINK_ROLES = ["admin", "platform_admin", "operations"];

interface FormValues {
  goods_code: string;
  goods_title: string;
  short_name?: string;
  season?: string;
  remark?: string;
  is_active?: boolean;
  items: { style_id: string; single_goods_cost?: number | null }[];
}

/** 款式弹窗：从成员款式点开编辑，或在成员选择器里就地新建（新建时记下要填回哪一行）。 */
interface StyleModalState {
  open: boolean;
  mode: "create" | "edit";
  styleId?: string;
  initialCode?: string;
  targetRow?: number;
}

/** 把表单里的数字成本转成后端要的字符串，空值保持 null 让后端回落到 SKU 成本价。 */
function toCost(v: number | null | undefined): string | null {
  return v === null || v === undefined ? null : String(v);
}

function fmtCost(v: string | null): string {
  return v === null ? "—" : `¥${Number(v).toFixed(2)}`;
}

function styleLabel(s: Pick<Style, "style_code" | "style_name">): string {
  return `${s.style_code} ${s.style_name}`;
}

/**
 * 商品 / 套装列表与编辑（商品 / 套装页的「商品 / 套装」页签）。
 *
 * 「商品」是店铺里卖的一个东西，也是报表归属的主体：投产报表按商品聚合，平台链接与推广
 * 记录都归到商品上。款式（货品）是另一层 —— 商品引用款式，套装就是引用了多个款式的商品。
 *
 * 单品与套装不分两个页面：它们的区别只有成员数量，``is_suit`` 由后端按成员数推导，
 * 列表里用标签区分即可。成员款式可以从这里直接打开编辑，挑成员时搜不到的款式可以就地新建。
 */
export function GoodsPanel() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<GoodsFilters>({
    page: 1,
    page_size: 20,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Goods | null>(null);
  const [linkGoods, setLinkGoods] = useState<Goods | null>(null);
  const [styleKeyword, setStyleKeyword] = useState("");
  const [styleModal, setStyleModal] = useState<StyleModalState>({
    open: false,
    mode: "create",
  });
  // 选中过的款式 ID → 显示文字。搜索结果随关键词变化，已选的行仍要显示货号 + 款名而不是 UUID。
  const [styleLabels, setStyleLabels] = useState<Record<string, string>>({});
  const [form] = Form.useForm<FormValues>();

  const { data, isLoading } = useQuery({
    queryKey: ["goods", filters],
    queryFn: () => listGoods(filters),
  });
  // 季节选项 = 字典 season 启用值 + 商品上已有的值（8a-3）；类目已下线，不再查类目字典
  const { data: seasons } = useQuery({
    queryKey: ["goods", "season-options"],
    queryFn: getGoodsSeasonOptions,
  });
  const seasonOptions = (seasons ?? []).map((s) => ({ label: s, value: s }));

  // 款式可能上百个，走服务端关键词搜索而不是全量拉下来。
  const { data: stylePickerData, isFetching: stylesFetching } = useQuery({
    queryKey: ["styles", "goods-picker", styleKeyword],
    queryFn: () =>
      listStyles({ page: 1, page_size: 20, keyword: styleKeyword || undefined }),
    enabled: open,
  });

  useEffect(() => {
    const items = stylePickerData?.items ?? [];
    if (items.length === 0) return;
    setStyleLabels((prev) => {
      const next = { ...prev };
      for (const s of items) next[s.id] = styleLabel(s);
      return next;
    });
  }, [stylePickerData]);

  const styleOptions = useMemo(
    () =>
      (stylePickerData?.items ?? []).map((s) => ({
        label: styleLabel(s),
        value: s.id,
      })),
    [stylePickerData]
  );

  /** 已选成员的显示文字：搜过的款式 > 编辑中商品自带的成员信息 > 原样。 */
  function memberLabel(styleId: string): string {
    if (styleLabels[styleId]) return styleLabels[styleId];
    const item = editing?.items.find((i) => i.style_id === styleId);
    if (item) return `${item.style_code ?? ""} ${item.style_name ?? ""}`.trim() || styleId;
    return styleId;
  }

  const saveMutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const items: GoodsStyleItemInput[] = (values.items ?? []).map((it, idx) => ({
        style_id: it.style_id,
        single_goods_cost: toCost(it.single_goods_cost),
        sort_order: idx,
      }));
      // 清空输入框 = 去掉简称；后端收到 null 会清掉，之后回落显示全称
      const shortName = values.short_name?.trim() || null;
      if (editing) {
        return updateGoods(editing.id, {
          goods_title: values.goods_title,
          short_name: shortName,
          season: values.season ?? null,
          remark: values.remark ?? null,
          is_active: values.is_active,
          items,
        });
      }
      return createGoods({
        goods_code: values.goods_code,
        goods_title: values.goods_title,
        short_name: shortName,
        season: values.season ?? null,
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
      short_name: record.short_name ?? undefined,
      season: record.season ?? undefined,
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

  function openStyleEdit(styleId: string) {
    setStyleModal({ open: true, mode: "edit", styleId });
  }

  function openStyleCreate(code: string, targetRow: number) {
    setStyleModal({ open: true, mode: "create", initialCode: code, targetRow });
  }

  function handleStyleSaved(style: Style) {
    setStyleLabels((prev) => ({ ...prev, [style.id]: styleLabel(style) }));
    if (styleModal.mode === "create" && styleModal.targetRow !== undefined) {
      // 新建的款式直接选进发起新建的那一行
      form.setFieldValue(["items", styleModal.targetRow, "style_id"], style.id);
      setStyleKeyword("");
    }
  }

  const columns: ColumnsType<Goods> = [
    {
      // 商品图由成员款式派生（8a-2）：单品 1 张，套装并排、缺图不占位
      title: "主图",
      dataIndex: "images",
      width: 104,
      render: (_, row) => (
        <GoodsImages
          images={row.images}
          displayName={goodsDisplayName(row.goods_title, row.short_name)}
        />
      ),
    },
    {
      title: "商品编码",
      dataIndex: "goods_code",
      width: 170,
      fixed: "left",
      render: (v: string) => <Typography.Text copyable>{v}</Typography.Text>,
    },
    {
      title: "商品简称",
      dataIndex: "short_name",
      width: 260,
      ellipsis: { showTitle: false },
      render: (_: string | null, row) => (
        <>
          {row.is_suit && (
            <Tag color="purple" style={{ marginInlineEnd: 4 }}>
              套装
            </Tag>
          )}
          <GoodsNameCell goodsTitle={row.goods_title} shortName={row.short_name} markMissing />
        </>
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
          <Space size={0} wrap split={<span aria-hidden="true">+</span>}>
            {row.items.map((i) => (
              <Tooltip key={i.style_id} title={i.style_name ? `编辑款式：${i.style_name}` : "编辑款式"}>
                <Button
                  type="link"
                  size="small"
                  style={{ paddingInline: 4 }}
                  onClick={() => openStyleEdit(i.style_id)}
                >
                  {i.style_code ?? "?"}
                </Button>
              </Tooltip>
            ))}
          </Space>
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
      width: 190,
      fixed: "right",
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          {/* 套装在这里绑千牛链接（8a-5）；与 /platform-links 的路由限制同一组角色，单品不加 */}
          {record.is_suit && (
            <PermissionGate requireAnyRole={LINK_ROLES}>
              <Button type="link" size="small" onClick={() => setLinkGoods(record)}>
                绑定链接
              </Button>
            </PermissionGate>
          )}
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

  const keyword = styleKeyword.trim();

  return (
    <>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        商品是报表归属的主体：投产报表按商品聚合，平台链接与推广费都归到商品上。
        引用了多个款式的商品自动记为套装，成本按成员相加。
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }} wrap>
        <Space wrap>
          <Input.Search
            placeholder="商品编码 / 简称 / 全称 / 成员货号 / 款名"
            allowClear
            style={{ width: 300 }}
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
            placeholder="季节"
            allowClear
            style={{ width: 120 }}
            options={seasonOptions}
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
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建商品
        </Button>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1700 }}
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
              name="short_name"
              label="商品简称"
              tooltip="列表、报表和下拉里显示这个短名字；不填就显示全称"
              style={{ width: 400 }}
            >
              <Input
                placeholder="如：冰雪飞狐皮草外套"
                maxLength={GOODS_SHORT_NAME_MAX_LEN}
                showCount
                allowClear
              />
            </Form.Item>
          </Space>

          <Form.Item
            name="goods_title"
            label="商品全称"
            tooltip="店铺里的完整标题。悬停商品名时显示，搜索时也能搜到"
            rules={[{ required: true, message: "请填商品全称" }]}
          >
            <Input placeholder="店铺里显示的完整标题" />
          </Form.Item>

          <Space align="start" style={{ display: "flex" }}>
            <Form.Item name="season" label="季节" style={{ width: 200 }}>
              <Select allowClear placeholder="选择季节" options={seasonOptions} />
            </Form.Item>
            {/* 品牌只读（8a-4，A12）：只由商品资料导入写入，这里不再提供选择 */}
            <Form.Item
              label="品牌"
              style={{ width: 240 }}
              extra="由商品资料导入写入，此处只读"
            >
              <Input value={editing?.brand_name || "—"} disabled aria-label="品牌（只读）" />
            </Form.Item>
          </Space>

          <Typography.Text strong>成员款式</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            挂 1 个款式是单品，挂 2 个以上自动记为套装。成本留空则取该款式 SKU 的成本价。
            搜不到的款式可以在下拉里直接新建。
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
                        labelRender={({ value, label }) =>
                          label ?? (value ? memberLabel(String(value)) : "")
                        }
                        notFoundContent={
                          stylesFetching ? (
                            "搜索中…"
                          ) : keyword ? (
                            <Button
                              type="link"
                              icon={<PlusOutlined />}
                              style={{ paddingInline: 0 }}
                              onClick={() => openStyleCreate(keyword, field.name)}
                            >
                              新建款式「{keyword}」
                            </Button>
                          ) : (
                            "暂无款式"
                          )
                        }
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

      <StyleEditModal
        open={styleModal.open}
        mode={styleModal.mode}
        styleId={styleModal.styleId}
        initialCode={styleModal.initialCode}
        onClose={() => setStyleModal((m) => ({ ...m, open: false }))}
        onSaved={handleStyleSaved}
      />

      <SuitLinkModal goods={linkGoods} onClose={() => setLinkGoods(null)} />
    </>
  );
}
