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
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, SearchOutlined, TagsOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  DOUYIN_METRIC_FIELDS,
  douyinMetricText,
  usesDouyinMetrics,
} from "@/features/blogger/douyinMetrics";
import { safeHomepageUrl } from "@/features/blogger/display";
import { BloggerTagDictModal } from "@/components/BloggerTagDict/BloggerTagDictModal";
import { useAuthStore } from "@/stores/authStore";
import {
  createBlogger,
  disableBlogger,
  listBloggerTags,
  listBloggers,
  restoreBlogger,
  updateBlogger,
} from "@/features/blogger/api";
import { toBloggerUpdate } from "@/features/blogger/edit";
import type {
  Blogger,
  BloggerCreate,
  BloggerListFilters,
} from "@/features/blogger/types";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { BloggerHoverCard } from "@/components/BloggerHoverCard/BloggerHoverCard";
import { PLATFORMS } from "@/features/common/platforms";

const TYPES = ["素人", "KOC", "KOL", "明星"];
const GENDER_TARGETS = ["女性", "男性", "中性"];
const LEVELS = ["A", "B", "C", "D"];
const CONTENT_CATEGORIES = ["单篇", "合集"];

// 灰豚爬虫/指标列（对齐 final.xlsx 博主库），从 crawler_metrics 按列名读取
// §5.4：3篇/7篇/14篇 的 阅读/点赞/收藏/评论 这组统一后移到「爆文率」之后
const CRAWLER_FIELDS = [
  "阅读点赞比", "收藏赞比", "近期数据涨的博主",
  "活跃粉丝数", "阅读粉丝数", "粉丝画像", "粉丝占比", "主要年龄",
  "粉丝赞藏比", "笔记数", "爆文率",
  "3篇阅读量", "3篇点赞数", "3篇收藏数", "3篇评论数",
  "7篇阅读量", "7篇点赞数", "7篇收藏数", "7篇评论数",
  "14篇阅读量", "14篇点赞数", "14篇收藏数", "14篇评论数",
  "3天平均阅读", "7天平均阅读", "14天平均阅读",
  "3天平均点赞", "7天平均点赞", "14天平均点赞",
  "3天阅读涨跌", "7天阅读涨跌", "3天点赞涨跌", "7天点赞涨跌",
];

// 8b §6.2：博主模版表头（别名：账号 = 小红书ID / 小红书号，昵称 = 小红书昵称，微信 = 微信号，粉丝数 = 粉丝量，类目标签 = 标签）
const MANUAL_BLOGGER_COLUMNS = [
  "账号", "昵称", "平台", "微信", "手机号", "粉丝数", "博主类型", "性别投放",
  "类目标签", "报价", "合作历史", "备注", "主页链接", "网页ID",
];
// 8b §6.3：灰豚抖音版式固定，只列读的列；r1 N8：列说明要 templateColumns 非空才显示
const DOUYIN_IMPORT_COLUMNS = ["博主ID", "抖音博主", "网页ID", "微信号", "报价", "粉丝总量", "统计列（灰豚指数 … 曝光点赞比）"];
const DOUYIN_IMPORT_NOTE =
  "直接传灰豚导出的 .xlsx 原文件，读『抖音博主库』sheet，7天视频详情暂不导入。按博主ID 判重，读这些列：";
// 报价（及报价备注）的默认可写角色，同后端 FIELD_PERMISSION_REGISTRY["blogger"]["quote"]；最终以后端校验为准
const QUOTE_WRITE_ROLES = ["admin", "platform_admin", "pr", "pr_manager"];
const CATEGORY_TAG_MAX = 20;

function isRecentGrowthValue(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) && value > 0;
  if (typeof value !== "string") return false;

  const normalized = value.trim().toLowerCase();
  if (["是", "上涨", "true", "1"].includes(normalized)) return true;
  return (
    /^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized) &&
    Number(normalized) > 0
  );
}

export function BloggerListPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<BloggerListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Blogger | null>(null);
  const [form] = Form.useForm<BloggerCreate>();
  const [tagDictOpen, setTagDictOpen] = useState(false);
  // 本页刚上传的博主导入批次，「标签字典 → 导入缺的标签」按它定位（r1 N14 改法）
  const [lastBloggerBatchId, setLastBloggerBatchId] = useState<string>();
  const user = useAuthStore((s) => s.user);
  const canEditQuote = Boolean(user?.roles.some((r) => QUOTE_WRITE_ROLES.includes(r)));

  const { data, isLoading } = useQuery({
    queryKey: ["bloggers", filters],
    queryFn: () => listBloggers(filters),
  });

  // 类目标签只能从字典里选；弹窗开着才查
  const tagDictQuery = useQuery({
    queryKey: ["blogger-tags"],
    queryFn: listBloggerTags,
    enabled: open,
  });
  const categoryTagOptions = (tagDictQuery.data?.items ?? []).map((t) => ({
    label: t.value,
    value: t.value,
  }));

  const saveMutation = useMutation({
    mutationFn: async (values: BloggerCreate) =>
      editing
        ? updateBlogger(editing.id, toBloggerUpdate(values, editing))
        : createBlogger(values),
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
    {
      title: "等级",
      dataIndex: "level",
      width: 70,
      render: (v: string | null) => (v ? <Tag color="blue">{v}</Tag> : "—"),
    },
    {
      title: "分类",
      dataIndex: "content_category",
      width: 80,
      render: (v) => v || "—",
    },
    {
      // 悬浮看最近合作过的款式：挑博主时不用再切页去翻推广单（PRD V1.4 改动 3）
      title: "昵称",
      dataIndex: "nickname",
      width: 140,
      render: (v: string, record: Blogger) => (
        <BloggerHoverCard bloggerId={record.id} bloggerName={v}>
          {v}
        </BloggerHoverCard>
      ),
    },
    // 8b-1：列名历史原因叫 xiaohongshu_id，语义是平台账号（抖音 = 灰豚博主ID）
    { title: "账号", dataIndex: "xiaohongshu_id", width: 130 },
    { title: "平台", dataIndex: "platform", width: 80 },
    { title: "网页ID", dataIndex: "web_id", width: 130, render: (v) => v || "—" },
    {
      title: "主页",
      dataIndex: "homepage_url",
      width: 70,
      render: (v: string | null | undefined, r: Blogger) => {
        const href = safeHomepageUrl(v);
        return href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`打开 ${r.nickname} 的主页（新窗口）`}
          >
            打开
          </a>
        ) : (
          "—"
        );
      },
    },
    { title: "微信号", dataIndex: "wechat", width: 110, render: (v) => v || "—" },
    {
      title: "报价",
      dataIndex: "quote",
      width: 100,
      render: (v: string | null) => (v == null ? "—" : `¥${v}`),
    },
    {
      // 与报价同一条字段权限，看不到报价的人收到 null
      title: "报价备注",
      dataIndex: "quote_note",
      width: 140,
      ellipsis: { showTitle: true },
      render: (v: string | null | undefined) => v || "—",
    },
    {
      title: "粉丝量",
      dataIndex: "follower_count",
      width: 100,
      render: (v: number | null) => (v == null ? "—" : v.toLocaleString()),
    },
    {
      title: "博主对接人(主)",
      dataIndex: "contact_primary",
      width: 130,
      render: (v) => v || "—",
    },
    {
      title: "博主对接人(备)",
      dataIndex: "contact_backup",
      width: 130,
      render: (v) => v || "—",
    },
    {
      title: "是否添加成功",
      dataIndex: "is_added_success",
      width: 110,
      render: (v: boolean) =>
        v ? <Tag color="green">是</Tag> : "—",
    },
    {
      title: "博主类型",
      dataIndex: "blogger_type",
      width: 90,
      render: (v) => v || "—",
    },
    {
      // 类目标签蓝色；质量标签系统自动计算，金色 + 锁形提示
      title: "标签",
      key: "tags",
      width: 180,
      render: (_: unknown, r: Blogger) =>
        r.category_tags.length || r.quality_tags.length ? (
          <Space size={[0, 4]} wrap>
            {r.category_tags.map((t) => (
              <Tag key={`c_${t}`} color="blue">
                {t}
              </Tag>
            ))}
            {r.quality_tags.map((t) => (
              <Tag key={`q_${t}`} color="gold" title="系统标签">
                {t}
              </Tag>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: "是否假号",
      dataIndex: "is_suspected_fake",
      width: 90,
      render: (v: boolean) => (v ? <Tag color="red">疑似</Tag> : "—"),
    },
    // 8b §7.4：平台筛「抖音」时换成灰豚抖音的统计列（读 platform_metrics.raw 原文），否则照旧
    ...(usesDouyinMetrics(filters.platform)
      ? DOUYIN_METRIC_FIELDS.map((f) => ({
          title: f,
          key: `pm_${f}`,
          width: 120,
          render: (_: unknown, r: Blogger) => douyinMetricText(r.platform_metrics, f),
        }))
      : CRAWLER_FIELDS.map((f) => ({
          title: f,
          key: `cm_${f}`,
          width: 110,
          render: (_: unknown, r: Blogger) => {
            const v = (r.crawler_metrics ?? {})[f];
            if (f === "近期数据涨的博主") {
              return isRecentGrowthValue(v) ? <Tag color="green">上涨</Tag> : "—";
            }
            return v == null || v === "" ? "—" : String(v);
          },
        }))),
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
      // 页头按钮多了，窄屏时标题保持原宽、按钮在右侧换行（否则标题被挤没）
      styles={{ title: { flex: "0 0 auto" }, extra: { minWidth: 0, padding: "8px 0 8px 12px" } }}
      extra={
        <Space wrap>
          <ImportUploadButton
            source="manual_blogger"
            label="导入博主库"
            invalidateKeys={[["bloggers"], ["blogger-tags"]]}
            templateColumns={MANUAL_BLOGGER_COLUMNS}
            onUploaded={setLastBloggerBatchId}
          />
          <ImportUploadButton
            source="huitun"
            label="导入灰豚画像"
            invalidateKeys={[["bloggers"]]}
          />
          <ImportUploadButton
            source="huitun_douyin"
            label="导入灰豚抖音博主"
            invalidateKeys={[["bloggers"]]}
            templateColumns={DOUYIN_IMPORT_COLUMNS}
            columnsNote={DOUYIN_IMPORT_NOTE}
          />
          <Button icon={<TagsOutlined />} onClick={() => setTagDictOpen(true)}>
            标签字典
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建博主
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索昵称 / 账号 / 网页ID"
          aria-label="搜索博主"
          allowClear
          style={{ width: 280, maxWidth: "100%" }}
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
        <Select
          placeholder="等级"
          allowClear
          style={{ width: 100 }}
          options={LEVELS.map((l) => ({ label: l, value: l }))}
          onChange={(v) => setFilters((f) => ({ ...f, level: v, page: 1 }))}
        />
        <Select
          aria-label="近期数据筛选"
          value={filters.recent_growth_only ? "up" : "all"}
          style={{ width: 120 }}
          options={[
            { label: "全部", value: "all" },
            { label: "上涨", value: "up" },
          ]}
          onChange={(value) =>
            setFilters((f) => ({
              ...f,
              recent_growth_only: value === "up" ? true : undefined,
              page: 1,
            }))
          }
        />
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 3200 }}
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
            label="账号"
            extra="抖音填灰豚的博主ID；同一平台下账号不能重复"
            rules={[{ required: true, message: "请输入账号" }]}
          >
            <Input placeholder="平台账号" maxLength={64} disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="nickname"
            label="昵称"
            rules={[{ required: true, message: "请输入昵称" }]}
          >
            <Input placeholder="博主昵称" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="level" label="等级">
              <Select
                allowClear
                placeholder="A/B/C/D"
                style={{ width: 140 }}
                options={LEVELS.map((l) => ({ label: l, value: l }))}
              />
            </Form.Item>
            <Form.Item name="content_category" label="分类">
              <Select
                allowClear
                placeholder="单篇/合集"
                style={{ width: 140 }}
                options={CONTENT_CATEGORIES.map((c) => ({ label: c, value: c }))}
              />
            </Form.Item>
          </Space>
          <Space size="large" align="end">
            <Form.Item name="contact_primary" label="博主对接人(主)">
              <Input placeholder="如 张三-1号机" style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="contact_primary_added" label="主-已添加" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Space size="large" align="end">
            <Form.Item name="contact_backup" label="博主对接人(备)">
              <Input placeholder="如 李四-2号机" style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="contact_backup_added" label="备-已添加" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
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
          <Form.Item name="web_id" label="网页ID">
            <Input placeholder="抖音网页ID（可选）" maxLength={64} />
          </Form.Item>
          <Form.Item
            name="homepage_url"
            label="主页链接"
            rules={[
              {
                pattern: /^\s*https?:\/\//i,
                message: "主页链接需以 http:// 或 https:// 开头",
              },
            ]}
          >
            <Input placeholder="https://…（可选）" maxLength={1024} />
          </Form.Item>
          <Form.Item
            name="category_tags"
            label="类目标签"
            extra="只能从标签字典里选；字典外的旧标签可以去掉"
            rules={[
              {
                type: "array",
                max: CATEGORY_TAG_MAX,
                message: `最多 ${CATEGORY_TAG_MAX} 个`,
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="从字典选择"
              loading={tagDictQuery.isLoading}
              options={categoryTagOptions}
              maxCount={CATEGORY_TAG_MAX}
            />
          </Form.Item>
          {canEditQuote ? (
            <Form.Item name="quote_note" label="报价备注" extra="报价原文，如「图文500」">
              <Input.TextArea rows={2} maxLength={500} placeholder="报价备注（可选）" />
            </Form.Item>
          ) : null}
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <BloggerTagDictModal
        open={tagDictOpen}
        onClose={() => setTagDictOpen(false)}
        batchId={lastBloggerBatchId}
      />
    </Card>
  );
}
