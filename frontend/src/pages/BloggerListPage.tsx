import { useState } from "react";
import {
  Button,
  Card,
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
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  createBlogger,
  disableBlogger,
  listBloggers,
  restoreBlogger,
  updateBlogger,
} from "@/features/blogger/api";
import type {
  Blogger,
  BloggerCreate,
  BloggerListFilters,
} from "@/features/blogger/types";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";

const PLATFORMS = ["小红书", "抖音", "快手", "B站"];
const TYPES = ["素人", "KOC", "KOL", "明星"];
const GENDER_TARGETS = ["女性", "男性", "中性"];

// 灰豚爬虫/指标列（对齐 final.xlsx 博主库），从 crawler_metrics 按列名读取
const CRAWLER_FIELDS = [
  "3篇阅读量", "3篇点赞数", "3篇收藏数", "3篇评论数",
  "7篇阅读量", "7篇点赞数", "7篇收藏数", "7篇评论数",
  "14篇阅读量", "14篇点赞数", "14篇收藏数", "14篇评论数",
  "阅读点赞比", "收藏赞比", "近期数据涨的博主",
  "活跃粉丝数", "阅读粉丝数", "粉丝画像", "粉丝占比", "主要年龄",
  "粉丝赞藏比", "笔记数", "爆文率",
  "3天平均阅读", "7天平均阅读", "14天平均阅读",
  "3天平均点赞", "7天平均点赞", "14天平均点赞",
  "3天阅读涨跌", "7天阅读涨跌", "3天点赞涨跌", "7天点赞涨跌",
];

export function BloggerListPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<BloggerListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Blogger | null>(null);
  const [form] = Form.useForm<BloggerCreate>();

  const { data, isLoading } = useQuery({
    queryKey: ["bloggers", filters],
    queryFn: () => listBloggers(filters),
  });

  const saveMutation = useMutation({
    mutationFn: async (values: BloggerCreate) =>
      editing ? updateBlogger(editing.id, values) : createBlogger(values),
    onSuccess: () => {
      message.success(editing ? "博主已更新" : "博主已创建");
      setOpen(false);
      setEditing(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["bloggers"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const toggleMutation = useMutation({
    mutationFn: (record: Blogger) =>
      record.is_active ? disableBlogger(record.id) : restoreBlogger(record.id),
    onSuccess: () => {
      message.success("操作成功");
      void qc.invalidateQueries({ queryKey: ["bloggers"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openCreate() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }
  function openEdit(record: Blogger) {
    setEditing(record);
    form.setFieldsValue(record as unknown as BloggerCreate);
    setOpen(true);
  }

  const columns: ColumnsType<Blogger> = [
    { title: "小红书昵称", dataIndex: "nickname", width: 140 },
    { title: "小红书ID", dataIndex: "xiaohongshu_id", width: 130 },
    { title: "平台", dataIndex: "platform", width: 80 },
    { title: "微信号", dataIndex: "wechat", width: 110, render: (v) => v || "—" },
    {
      title: "报价",
      dataIndex: "quote",
      width: 100,
      render: (v: string | null) => (v == null ? "—" : `¥${v}`),
    },
    {
      title: "粉丝量",
      dataIndex: "follower_count",
      width: 100,
      render: (v: number | null) => (v == null ? "—" : v.toLocaleString()),
    },
    {
      title: "博主类型",
      dataIndex: "blogger_type",
      width: 90,
      render: (v) => v || "—",
    },
    {
      title: "是否假号",
      dataIndex: "is_suspected_fake",
      width: 90,
      render: (v: boolean) => (v ? <Tag color="red">疑似</Tag> : "—"),
    },
    ...CRAWLER_FIELDS.map((f) => ({
      title: f,
      key: `cm_${f}`,
      width: 110,
      render: (_: unknown, r: Blogger) => {
        const v = (r.crawler_metrics ?? {})[f];
        return v == null || v === "" ? "—" : String(v);
      },
    })),
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      fixed: "right" as const,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 140,
      fixed: "right" as const,
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
      title={<Typography.Title level={4} style={{ margin: 0 }}>博主管理</Typography.Title>}
      extra={
        <Space>
          <ImportUploadButton
            source="manual_blogger"
            label="导入博主库"
            invalidateKeys={[["bloggers"]]}
            templateColumns={["小红书ID", "小红书昵称", "平台", "微信号", "报价", "粉丝量", "博主类型"]}
          />
          <ImportUploadButton
            source="huitun"
            label="导入灰豚画像"
            invalidateKeys={[["bloggers"]]}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建博主
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索昵称 / 小红书ID"
          allowClear
          style={{ width: 220 }}
          enterButton={<SearchOutlined />}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="平台"
          allowClear
          style={{ width: 120 }}
          options={PLATFORMS.map((p) => ({ label: p, value: p }))}
          onChange={(v) => setFilters((f) => ({ ...f, platform: v, page: 1 }))}
        />
        <Select
          placeholder="类型"
          allowClear
          style={{ width: 120 }}
          options={TYPES.map((t) => ({ label: t, value: t }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, blogger_type: v, page: 1 }))
          }
        />
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 2600 }}
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
        title={editing ? "编辑博主" : "新建博主"}
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
          initialValues={{ platform: "小红书" }}
        >
          <Form.Item
            name="xiaohongshu_id"
            label="小红书ID"
            rules={[{ required: true, message: "请输入小红书ID" }]}
          >
            <Input placeholder="小红书账号 ID" disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="nickname"
            label="昵称"
            rules={[{ required: true, message: "请输入昵称" }]}
          >
            <Input placeholder="博主昵称" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="platform" label="平台">
              <Select
                style={{ width: 140 }}
                options={PLATFORMS.map((p) => ({ label: p, value: p }))}
              />
            </Form.Item>
            <Form.Item name="blogger_type" label="类型">
              <Select
                allowClear
                placeholder="类型"
                style={{ width: 140 }}
                options={TYPES.map((t) => ({ label: t, value: t }))}
              />
            </Form.Item>
            <Form.Item name="gender_target" label="受众性别">
              <Select
                allowClear
                placeholder="性别"
                style={{ width: 140 }}
                options={GENDER_TARGETS.map((g) => ({ label: g, value: g }))}
              />
            </Form.Item>
          </Space>
          <Space size="large">
            <Form.Item name="follower_count" label="粉丝数">
              <InputNumber min={0} style={{ width: 180 }} placeholder="粉丝数" />
            </Form.Item>
            <Form.Item name="wechat" label="微信">
              <Input placeholder="微信号（可选）" style={{ width: 180 }} />
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
