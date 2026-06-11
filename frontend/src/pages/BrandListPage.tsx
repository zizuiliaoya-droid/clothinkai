import { useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createBrand,
  disableBrand,
  listBrands,
  updateBrand,
} from "@/features/product/api";
import type { Brand, BrandCreate } from "@/features/product/types";
import { extractErrorMessage } from "@/services/apiClient";

export function BrandListPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Brand | null>(null);
  const [form] = Form.useForm<BrandCreate & { is_active?: boolean }>();

  const { data, isLoading } = useQuery({
    queryKey: ["brands"],
    queryFn: () => listBrands({ page: 1, page_size: 100 }),
  });

  const saveMutation = useMutation({
    mutationFn: async (values: BrandCreate & { is_active?: boolean }) => {
      if (editing) {
        return updateBrand(editing.id, {
          brand_name: values.brand_name,
          is_active: values.is_active,
        });
      }
      return createBrand({
        brand_code: values.brand_code,
        brand_name: values.brand_name,
      });
    },
    onSuccess: () => {
      message.success(editing ? "品牌已更新" : "品牌已创建");
      setOpen(false);
      setEditing(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["brands"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const disableMutation = useMutation({
    mutationFn: (id: string) => disableBrand(id),
    onSuccess: () => {
      message.success("品牌已停用");
      void qc.invalidateQueries({ queryKey: ["brands"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openCreate() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }

  function openEdit(record: Brand) {
    setEditing(record);
    form.setFieldsValue(record);
    setOpen(true);
  }

  const columns: ColumnsType<Brand> = [
    { title: "品牌编码", dataIndex: "brand_code", width: 160 },
    { title: "品牌名称", dataIndex: "brand_name" },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 160,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          {record.is_active && (
            <Popconfirm
              title="确定停用该品牌？"
              onConfirm={() => disableMutation.mutate(record.id)}
            >
              <Button type="link" size="small" danger>
                停用
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>品牌管理</Typography.Title>}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建品牌
        </Button>
      }
    >
      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={false}
      />

      <Modal
        title={editing ? "编辑品牌" : "新建品牌"}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => saveMutation.mutate(v)}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            name="brand_code"
            label="品牌编码"
            rules={[
              { required: true, message: "请输入品牌编码" },
              { pattern: /^[A-Za-z0-9_-]+$/, message: "仅允许字母数字下划线连字符" },
            ]}
          >
            <Input placeholder="如 BRANDA" disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="brand_name"
            label="品牌名称"
            rules={[{ required: true, message: "请输入品牌名称" }]}
          >
            <Input placeholder="品牌中文名" />
          </Form.Item>
          {editing && (
            <Form.Item name="is_active" label="状态" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
