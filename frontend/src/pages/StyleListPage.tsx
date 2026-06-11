import { useState } from "react";
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
  Typography,
  message,
} from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createStyle,
  disableStyle,
  listBrands,
  listStyles,
  restoreStyle,
  updateStyle,
} from "@/features/product/api";
import type {
  Category,
  Style,
  StyleCreate,
  StyleListFilters,
} from "@/features/product/types";
import { extractErrorMessage } from "@/services/apiClient";

const CATEGORIES: Category[] = [
  "连衣裙",
  "上衣",
  "裤装",
  "裙装",
  "外套",
  "套装",
  "配饰",
];
const SEASONS = ["春", "夏", "秋", "冬", "四季"];
const GENDERS = ["女", "男", "中性", "童"];

export function StyleListPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<StyleListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Style | null>(null);
  const [form] = Form.useForm<StyleCreate>();

  const { data, isLoading } = useQuery({
    queryKey: ["styles", filters],
    queryFn: () => listStyles(filters),
  });

  const { data: brands } = useQuery({
    queryKey: ["brands", "options"],
    queryFn: () => listBrands({ page: 1, page_size: 100, is_active: true }),
  });

  const brandOptions =
    brands?.items.map((b) => ({ label: b.brand_name, value: b.id })) ?? [];

  const saveMutation = useMutation({
    mutationFn: async (values: StyleCreate) => {
      if (editing) return updateStyle(editing.id, values);
      return createStyle(values);
    },
    onSuccess: () => {
      message.success(editing ? "款式已更新" : "款式已创建");
      setOpen(false);
      setEditing(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["styles"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const toggleMutation = useMutation({
    mutationFn: (record: Style) =>
      record.is_active ? disableStyle(record.id) : restoreStyle(record.id),
    onSuccess: () => {
      message.success("操作成功");
      void qc.invalidateQueries({ queryKey: ["styles"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openCreate() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }

  function openEdit(record: Style) {
    setEditing(record);
    form.setFieldsValue({
      ...record,
      category: record.category as Category,
    } as StyleCreate);
    setOpen(true);
  }

  const columns: ColumnsType<Style> = [
    { title: "款号", dataIndex: "style_code", width: 140 },
    { title: "款名", dataIndex: "style_name" },
    { title: "简称", dataIndex: "short_name", render: (v) => v || "—" },
    { title: "类目", dataIndex: "category", width: 90 },
    { title: "季节", dataIndex: "season", width: 70, render: (v) => v || "—" },
    {
      title: "设计状态",
      dataIndex: "design_status",
      width: 100,
      render: (v: string) => (
        <Tag color={v === "大货" ? "blue" : "orange"}>{v}</Tag>
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
            onClick={() => toggleMutation.mutate(record)}
          >
            {record.is_active ? "停用" : "恢复"}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>款式管理</Typography.Title>}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建款式
        </Button>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索款号 / 款名"
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
          options={CATEGORIES.map((c) => ({ label: c, value: c }))}
          onChange={(v) => setFilters((f) => ({ ...f, category: v, page: 1 }))}
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
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
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
        onCancel={() => setOpen(false)}
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
          initialValues={{ design_status: "设计中" }}
        >
          <Form.Item
            name="style_code"
            label="款号"
            rules={[{ required: true, message: "请输入款号" }]}
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
          <Form.Item name="short_name" label="简称">
            <Input placeholder="商品简称（可选）" />
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
              options={CATEGORIES.map((c) => ({ label: c, value: c }))}
            />
          </Form.Item>
          <Space size="large">
            <Form.Item name="season" label="季节">
              <Select
                allowClear
                placeholder="季节"
                style={{ width: 140 }}
                options={SEASONS.map((s) => ({ label: s, value: s }))}
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
            <Form.Item name="design_status" label="设计状态">
              <Select
                style={{ width: 140 }}
                options={[
                  { label: "设计中", value: "设计中" },
                  { label: "大货", value: "大货" },
                ]}
              />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
