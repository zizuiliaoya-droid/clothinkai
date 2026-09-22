import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import {
  DeleteOutlined,
  PlusOutlined,
  SearchOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createStyle,
  disableStyle,
  enableStyle,
  listBrands,
  listDictItems,
  listStyles,
  removeStyleMainImage,
  updateStyle,
  uploadStyleMainImage,
} from "@/features/product/api";
import type {
  Style,
  StyleCreate,
  StyleListFilters,
} from "@/features/product/types";
import {
  compressStyleMainImage,
  formatImageKilobytes,
  type CompressedStyleImage,
} from "@/features/product/imageCompression";
import { extractErrorMessage } from "@/services/apiClient";
import { DictManagerModal } from "@/components/DictManager/DictManagerModal";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";

const GENDERS = ["女", "男", "中性", "童"];

type StatusFilter = "active" | "inactive" | "all";

/** 状态下拉 → 后端三态参数。后端：都不传=仅启用，is_active=false=仅停用，include_inactive=true=全部。 */
function statusParams(status: StatusFilter): Pick<
  StyleListFilters,
  "is_active" | "include_inactive"
> {
  if (status === "inactive") return { is_active: false, include_inactive: true };
  if (status === "all") return { is_active: undefined, include_inactive: true };
  return { is_active: undefined, include_inactive: undefined };
}

export function StyleListPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active");
  const [filters, setFilters] = useState<StyleListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [dictOpen, setDictOpen] = useState(false);
  const [editing, setEditing] = useState<Style | null>(null);
  const [compressedImage, setCompressedImage] = useState<CompressedStyleImage | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [removeExistingImage, setRemoveExistingImage] = useState(false);
  const [isCompressing, setIsCompressing] = useState(false);
  const [form] = Form.useForm<StyleCreate>();

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  const { data, isLoading } = useQuery({
    queryKey: ["styles", filters],
    queryFn: () => listStyles(filters),
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
  const { data: colors } = useQuery({
    queryKey: ["dict-items", "color"],
    queryFn: () => listDictItems("color"),
  });

  const categoryOptions = (categories ?? []).map((c) => ({ label: c.value, value: c.value }));
  const seasonOptions = (seasons ?? []).map((s) => ({ label: s.value, value: s.value }));
  const colorOptions = (colors ?? []).map((c) => ({ label: c.value, value: c.value }));
  const brandOptions =
    brands?.items.map((b) => ({ label: b.brand_name, value: b.id })) ?? [];

  function resetImageSelection() {
    setCompressedImage(null);
    setImagePreviewUrl(null);
    setRemoveExistingImage(false);
    setIsCompressing(false);
  }

  async function selectMainImage(file: File) {
    setIsCompressing(true);
    try {
      const compressed = await compressStyleMainImage(file);
      setCompressedImage(compressed);
      setImagePreviewUrl(URL.createObjectURL(compressed.file));
      setRemoveExistingImage(false);
      message.success(`主图已压缩至 ${formatImageKilobytes(compressed.compressedBytes)}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "主图压缩失败");
    } finally {
      setIsCompressing(false);
    }
  }

  const saveMutation = useMutation({
    mutationFn: async (values: StyleCreate) => {
      let saved = editing
        ? await updateStyle(editing.id, values)
        : await createStyle(values);
      if (compressedImage) {
        saved = await uploadStyleMainImage(saved.id, compressedImage.file);
      } else if (editing?.main_image_key && removeExistingImage) {
        await removeStyleMainImage(saved.id);
      }
      return saved;
    },
    onSuccess: () => {
      message.success(editing ? "款式已更新" : "款式已创建");
      setOpen(false);
      setEditing(null);
      resetImageSelection();
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["styles"] });
      void qc.invalidateQueries({ queryKey: ["production"] });
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  // 停用 ↔ 启用 走 disable/enable（操作 is_active）。
  // 不要用 restoreStyle —— 那个是恢复软删（is_deleted），对未软删的款式会直接报错。
  const toggleMutation = useMutation({
    mutationFn: (record: Style) =>
      record.is_active ? disableStyle(record.id) : enableStyle(record.id),
    onSuccess: (_data, record) => {
      message.success(record.is_active ? "款式已停用" : "款式已启用");
      void qc.invalidateQueries({ queryKey: ["styles"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeModal() {
    setOpen(false);
    setEditing(null);
    resetImageSelection();
    form.resetFields();
  }

  function openCreate() {
    setEditing(null);
    resetImageSelection();
    form.resetFields();
    setOpen(true);
  }

  function openEdit(record: Style) {
    setEditing(record);
    resetImageSelection();
    form.setFieldsValue({
      style_code: record.style_code,
      style_name: record.style_name,
      qianniu_product_id: record.qianniu_product_id,
      brand_id: record.brand_id,
      category: record.category,
      season: record.season,
      gender: record.gender as StyleCreate["gender"],
      tag_color: record.tag_color ?? [],
      remark: record.remark,
    });
    setOpen(true);
  }

  const displayedMainImageUrl =
    imagePreviewUrl ?? (!removeExistingImage ? editing?.main_image_url : null);

  const columns: ColumnsType<Style> = [
    {
      title: "主图",
      dataIndex: "main_image_url",
      width: 72,
      fixed: "left",
      render: (src: string | null, record) => (
        <StyleImageThumbnail src={src} alt={`${record.style_code} 款式主图`} />
      ),
    },
    { title: "货号", dataIndex: "style_code", width: 140, fixed: "left" },
    { title: "款名", dataIndex: "style_name" },
    { title: "千牛商品ID", dataIndex: "qianniu_product_id", width: 130, render: (v) => v || "—" },
    {
      title: "套装名称",
      dataIndex: "suite_name",
      width: 200,
      render: (v: string | null) =>
        v ? (
          <Tooltip title={v}>
            <Tag color="blue" style={{ maxWidth: 184, overflow: "hidden", textOverflow: "ellipsis" }}>
              {v}
            </Tag>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">单件</Typography.Text>
        ),
    },
    { title: "类目", dataIndex: "category", width: 90 },
    { title: "季节", dataIndex: "season", width: 70, render: (v) => v || "—" },
    {
      title: "颜色明细",
      dataIndex: "tag_color",
      width: 180,
      render: (v: string[] | null) =>
        v && v.length > 0 ? (
          <Space size={[0, 4]} wrap>
            {v.map((c) => (
              <Tag key={c} style={{ marginInlineEnd: 4 }}>
                {c}
              </Tag>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 90,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 150,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger={record.is_active}
            loading={toggleMutation.isPending}
            onClick={() => toggleMutation.mutate(record)}
          >
            {record.is_active ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>款式管理</Typography.Title>}
      extra={
        <Space>
          <Button onClick={() => setDictOpen(true)}>管理字典</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建款式
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索货号 / 款名"
          allowClear
          style={{ width: 220 }}
          enterButton={<SearchOutlined />}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="类目"
          allowClear
          style={{ width: 120 }}
          options={categoryOptions}
          onChange={(v) => setFilters((f) => ({ ...f, category: v, page: 1 }))}
        />
        <Select
          placeholder="季节"
          allowClear
          style={{ width: 120 }}
          options={seasonOptions}
          onChange={(v) => setFilters((f) => ({ ...f, season: v, page: 1 }))}
        />
        <Select
          placeholder="品牌"
          allowClear
          style={{ width: 160 }}
          options={brandOptions}
          onChange={(v) => setFilters((f) => ({ ...f, brand_id: v, page: 1 }))}
        />
        <Select
          value={statusFilter}
          style={{ width: 120 }}
          options={[
            { label: "仅启用", value: "active" },
            { label: "仅停用", value: "inactive" },
            { label: "全部", value: "all" },
          ]}
          onChange={(v: StatusFilter) => {
            setStatusFilter(v);
            setFilters((f) => ({ ...f, ...statusParams(v), page: 1 }));
          }}
        />
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1500 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 10,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <Modal
        title={editing ? "编辑款式" : "新建款式"}
        open={open}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending || isCompressing}
        okButtonProps={{ disabled: isCompressing }}
        destroyOnHidden
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) =>
            // 多选清空后 antd 给 undefined，axios 会整个省掉该字段 →
            // 后端 PATCH 语义视为"未修改"，颜色清不掉。这里显式归一为空数组。
            saveMutation.mutate({ ...v, tag_color: v.tag_color ?? [] })
          }
          style={{ marginTop: 16 }}
        >
          <Form.Item
            name="style_code"
            label="货号"
            rules={[{ required: true, message: "请输入货号" }]}
          >
            <Input placeholder="如 A001" disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="style_name"
            label="款名"
            rules={[{ required: true, message: "请输入款名" }]}
          >
            <Input placeholder="款式名称" />
          </Form.Item>
          <Form.Item
            name="qianniu_product_id"
            label="千牛商品ID"
            extra="用于关联投产数据。多个款式填相同的千牛ID 即视为一个套装，套装名称自动按货号顺序拼接款名。"
          >
            <Input placeholder="生意参谋商品ID（可选）" allowClear />
          </Form.Item>
          <Form.Item label="款式主图">
            <Space align="start" size="middle" wrap>
              <StyleImageThumbnail
                src={displayedMainImageUrl}
                alt={`${form.getFieldValue("style_code") || editing?.style_code || "款式"} 主图预览`}
                size={88}
              />
              <Space direction="vertical" size={8}>
                <Upload
                  accept="image/jpeg,image/png,image/webp"
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void selectMainImage(file);
                    return false;
                  }}
                >
                  <Button icon={<UploadOutlined />} loading={isCompressing}>
                    {displayedMainImageUrl ? "替换主图" : "选择主图"}
                  </Button>
                </Upload>
                {displayedMainImageUrl ? (
                  <Button
                    danger
                    type="text"
                    icon={<DeleteOutlined />}
                    disabled={isCompressing}
                    onClick={() => {
                      setCompressedImage(null);
                      setImagePreviewUrl(null);
                      setRemoveExistingImage(Boolean(editing?.main_image_key));
                    }}
                  >
                    移除主图
                  </Button>
                ) : null}
                <Typography.Text type="secondary">
                  JPG、PNG 或 WebP；最长边压缩至 1600px，保存文件严格小于 300KB。
                </Typography.Text>
                {compressedImage ? (
                  <Typography.Text type="success" role="status">
                    已压缩：{formatImageKilobytes(compressedImage.originalBytes)} → {formatImageKilobytes(compressedImage.compressedBytes)}
                    （{compressedImage.width}×{compressedImage.height}）
                  </Typography.Text>
                ) : null}
                {removeExistingImage ? (
                  <Typography.Text type="warning" role="status">
                    保存后将移除当前主图。
                  </Typography.Text>
                ) : null}
              </Space>
            </Space>
          </Form.Item>
          <Form.Item name="brand_id" label="品牌">
            <Select allowClear placeholder="选择品牌" options={brandOptions} />
          </Form.Item>
          <Form.Item
            name="category"
            label="类目"
            rules={[{ required: true, message: "请选择类目" }]}
          >
            <Select
              placeholder="选择类目"
              options={categoryOptions}
            />
          </Form.Item>
          <Space size="large">
            <Form.Item name="season" label="季节">
              <Select
                allowClear
                placeholder="季节"
                style={{ width: 140 }}
                options={seasonOptions}
              />
            </Form.Item>
            <Form.Item name="gender" label="适用性别">
              <Select
                allowClear
                placeholder="性别"
                style={{ width: 140 }}
                options={GENDERS.map((g) => ({ label: g, value: g }))}
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="tag_color"
            label="颜色明细"
            extra="该款实际生产的颜色，可多选。颜色值在上方「管理字典」→「颜色」中维护。"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择颜色（可多选）"
              options={colorOptions}
              notFoundContent="暂无颜色，请先在「管理字典」中添加"
            />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <DictManagerModal open={dictOpen} onClose={() => setDictOpen(false)} />
    </Card>
  );
}
