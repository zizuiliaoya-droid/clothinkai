import { useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { DownOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  cancelPromotion,
  createPromotion,
  listPromotions,
  publishPromotion,
  reviewPromotion,
} from "@/features/promotion/api";
import type {
  Promotion,
  PromotionCreate,
  PromotionListFilters,
} from "@/features/promotion/types";
import { listStyles } from "@/features/product/api";
import { listBloggers } from "@/features/blogger/api";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";

const PLATFORMS = ["小红书", "抖音", "快手", "B站"];
const PUBLISH_STATUS = ["未发布", "已发布", "已取消", "异常", "已删除"];

// 站外推广人工源列（对齐 final.xlsx），从 source_extra 读取
const SOURCE_FIELDS = [
  "颜色及规格", "打单地址", "发货单号", "订单号", "寄回单号",
  "合作方式", "合作形式", "收藏数", "评论数", "博主风格", "买家秀",
];

const statusColor: Record<string, string> = {
  未发布: "default",
  已发布: "green",
  已取消: "orange",
  异常: "red",
  已删除: "red",
};

export function PromotionListPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<PromotionListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<Promotion | null>(null);
  const [publishForm] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ["promotions", filters],
    queryFn: () => listPromotions(filters),
  });
  const { data: styles } = useQuery({
    queryKey: ["styles", "options"],
    queryFn: () => listStyles({ page: 1, page_size: 100 }),
  });
  const { data: bloggers } = useQuery({
    queryKey: ["bloggers", "options"],
    queryFn: () => listBloggers({ page: 1, page_size: 100 }),
  });

  const styleOptions =
    styles?.items.map((s) => ({
      label: `${s.style_code} ${s.style_name}`,
      value: s.id,
    })) ?? [];
  const bloggerOptions =
    bloggers?.items.map((b) => ({
      label: `${b.nickname} (${b.xiaohongshu_id})`,
      value: b.id,
    })) ?? [];

  const createMutation = useMutation({
    mutationFn: (values: PromotionCreate) => createPromotion(values),
    onSuccess: () => {
      message.success("推广已创建");
      setOpen(false);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const publishMutation = useMutation({
    mutationFn: ({
      id,
      publish_url,
      actual_publish_date,
    }: {
      id: string;
      publish_url: string;
      actual_publish_date: string;
    }) => publishPromotion(id, { publish_url, actual_publish_date }),
    onSuccess: () => {
      message.success("已标记发布");
      setPublishOpen(false);
      setPublishTarget(null);
      publishForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openPublish(record: Promotion) {
    setPublishTarget(record);
    publishForm.resetFields();
    publishForm.setFieldsValue({ actual_publish_date: dayjs() });
    setPublishOpen(true);
  }

  const cancelMutation = useMutation({
    mutationFn: (id: string) =>
      cancelPromotion(id, { cancel_reason: "手动取消" }),
    onSuccess: () => {
      message.success("已取消");
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const reviewMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      reviewPromotion(id, { action }),
    onSuccess: () => {
      message.success("审核完成");
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function handleCreate(values: Record<string, unknown>) {
    const payload: PromotionCreate = {
      style_id: values.style_id as string,
      blogger_id: values.blogger_id as string,
      platform: values.platform as string,
      cooperation_date: dayjs(values.cooperation_date as dayjs.Dayjs).format(
        "YYYY-MM-DD"
      ),
      quote_amount:
        values.quote_amount != null ? String(values.quote_amount) : null,
      note_title: (values.note_title as string) || null,
      remark: (values.remark as string) || null,
    };
    createMutation.mutate(payload);
  }

  const columns: ColumnsType<Promotion> = [
    { title: "内部编码", dataIndex: "internal_code", width: 150 },
    { title: "货号", dataIndex: "style_code_snapshot", width: 110 },
    { title: "品名", dataIndex: "style_short_name_snapshot", width: 130, render: (v) => v || "—" },
    { title: "合作平台", dataIndex: "platform", width: 90 },
    { title: "合作日期", dataIndex: "cooperation_date", width: 110 },
    {
      title: "预定发布日期",
      dataIndex: "scheduled_publish_date",
      width: 120,
      render: (v) => v || "—",
    },
    {
      title: "报价",
      dataIndex: "quote_amount",
      width: 90,
      render: (v: string | null) => (v == null ? "—" : `¥${v}`),
    },
    {
      title: "是否催发",
      dataIndex: "urge_status",
      width: 100,
      render: (v: string | null) =>
        v ? (
          <Tag color={v === "超时" || v === "重要催发" ? "red" : v === "催发" ? "orange" : "default"}>
            {v}
          </Tag>
        ) : (
          "—"
        ),
    },
    {
      title: "是否发布",
      dataIndex: "publish_status",
      width: 100,
      render: (v: string) => <Tag color={statusColor[v]}>{v}</Tag>,
    },
    {
      title: "点赞量",
      dataIndex: "like_count",
      width: 90,
      render: (v: number | null) => (v == null ? "—" : v),
    },
    {
      title: "结算状态",
      dataIndex: "settlement_status",
      width: 100,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    ...SOURCE_FIELDS.map((f) => ({
      title: f,
      key: `se_${f}`,
      width: 110,
      render: (_: unknown, r: Promotion) => {
        const v = (r.source_extra ?? {})[f];
        return v == null || v === "" ? "—" : String(v);
      },
    })),
    {
      title: "操作",
      width: 110,
      fixed: "right",
      render: (_, record) => {
        const items = [
          {
            key: "publish",
            label: "标记发布",
            disabled: record.publish_status !== "未发布",
            onClick: () => openPublish(record),
          },
          {
            key: "cancel",
            label: "取消",
            disabled: record.publish_status !== "未发布",
            onClick: () => cancelMutation.mutate(record.id),
          },
          {
            key: "approve",
            label: "审核通过",
            onClick: () =>
              reviewMutation.mutate({ id: record.id, action: "approve" }),
          },
          {
            key: "reject",
            label: "审核驳回",
            danger: true,
            onClick: () =>
              reviewMutation.mutate({ id: record.id, action: "reject" }),
          },
        ];
        return (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="link" size="small">
              操作 <DownOutlined />
            </Button>
          </Dropdown>
        );
      },
    },
  ];

  return (
    <Card
      title={<Typography.Title level={4} style={{ margin: 0 }}>推广管理</Typography.Title>}
      extra={
        <Space>
          <ImportUploadButton
            source="manual_promotion"
            label="导入站外推广"
            invalidateKeys={[["promotions"]]}
            templateColumns={SOURCE_FIELDS}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              form.resetFields();
              setOpen(true);
            }}
          >
            新建推广
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索内部编码 / 款号"
          allowClear
          style={{ width: 220 }}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="发布状态"
          allowClear
          style={{ width: 130 }}
          options={PUBLISH_STATUS.map((s) => ({ label: s, value: s }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, publish_status: v, page: 1 }))
          }
        />
        <Select
          placeholder="平台"
          allowClear
          style={{ width: 120 }}
          options={PLATFORMS.map((p) => ({ label: p, value: p }))}
          onChange={(v) => setFilters((f) => ({ ...f, platform: v, page: 1 }))}
        />
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 2400 }}
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
        title="新建推广"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          style={{ marginTop: 16 }}
          initialValues={{ platform: "小红书", cooperation_date: dayjs() }}
        >
          <Form.Item
            name="style_id"
            label="款式"
            rules={[{ required: true, message: "请选择款式" }]}
          >
            <Select
              showSearch
              placeholder="选择款式"
              options={styleOptions}
              filterOption={(i, o) =>
                (o?.label ?? "").toString().includes(i)
              }
            />
          </Form.Item>
          <Form.Item
            name="blogger_id"
            label="博主"
            rules={[{ required: true, message: "请选择博主" }]}
          >
            <Select
              showSearch
              placeholder="选择博主"
              options={bloggerOptions}
              filterOption={(i, o) =>
                (o?.label ?? "").toString().includes(i)
              }
            />
          </Form.Item>
          <Space size="large">
            <Form.Item
              name="platform"
              label="平台"
              rules={[{ required: true }]}
            >
              <Select
                style={{ width: 160 }}
                options={PLATFORMS.map((p) => ({ label: p, value: p }))}
              />
            </Form.Item>
            <Form.Item
              name="cooperation_date"
              label="合作日期"
              rules={[{ required: true, message: "请选择合作日期" }]}
            >
              <DatePicker style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item name="quote_amount" label="报价金额">
            <InputNumber
              min={0}
              precision={2}
              style={{ width: "100%" }}
              placeholder="报价（可选）"
              prefix="¥"
            />
          </Form.Item>
          <Form.Item name="note_title" label="笔记标题">
            <Input placeholder="笔记标题（可选）" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="标记发布"
        open={publishOpen}
        onCancel={() => setPublishOpen(false)}
        onOk={() => publishForm.submit()}
        confirmLoading={publishMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={publishForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v) => {
            if (!publishTarget) return;
            publishMutation.mutate({
              id: publishTarget.id,
              publish_url: v.publish_url,
              actual_publish_date: dayjs(v.actual_publish_date).format(
                "YYYY-MM-DD"
              ),
            });
          }}
        >
          <Form.Item
            name="publish_url"
            label="发布链接"
            rules={[
              { required: true, message: "请输入发布链接" },
              { type: "url", message: "请输入合法 URL" },
            ]}
          >
            <Input placeholder="https://www.xiaohongshu.com/..." />
          </Form.Item>
          <Form.Item
            name="actual_publish_date"
            label="实际发布日期"
            rules={[{ required: true, message: "请选择发布日期" }]}
          >
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
