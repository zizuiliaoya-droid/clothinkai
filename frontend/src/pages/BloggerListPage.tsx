import { useReadOnlySupport } from "@/stores/supportStore";
import { formatMoney } from "@/utils/money";
import { useState } from "react";
import {
  Button,
  Alert,
  Card,
  Form,
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
import { PlusOutlined, SearchOutlined, TagsOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { extractHomepageUrl, safeHomepageUrl } from "@/features/blogger/display";
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
import { BloggerLegacyMetrics } from "@/features/blogger/BloggerLegacyMetrics";
import { BloggerHoverCard } from "@/components/BloggerHoverCard/BloggerHoverCard";
import { PLATFORMS } from "@/features/common/platforms";
import { BloggerClaimsModal } from "@/features/blogger/BloggerClaimsModal";
import { WorkWechatModal } from "@/features/blogger/WorkWechatModal";
import { enableClaims, getClaimSettings, listWorkWechats } from "@/features/blogger/claims";

const TYPES = ["素人", "KOC", "KOL", "明星"];
const GENDER_TARGETS = ["女性", "男性", "中性"];
const LEVELS = ["A", "B", "C", "D"];
// 8b §6.2：博主模版表头（别名：账号 = 小红书ID / 小红书号，昵称 = 小红书昵称，微信 = 微信号，粉丝数 = 粉丝量，类目标签 = 标签）
const MANUAL_BLOGGER_COLUMNS = [
  "账号", "昵称", "平台", "微信", "手机号", "粉丝数", "博主类型", "性别投放",
  "类目标签", "报价", "合作历史", "备注", "主页链接", "网页ID",
];
// 报价（及报价备注）的默认可写角色，同后端 FIELD_PERMISSION_REGISTRY["blogger"]["quote"]；最终以后端校验为准
const QUOTE_WRITE_ROLES = ["admin", "platform_admin", "pr", "pr_manager"];
const CATEGORY_TAG_MAX = 20;

export function BloggerListPage() {
  const readOnly = useReadOnlySupport();
  const qc = useQueryClient();
  const [filters, setFilters] = useState<BloggerListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Blogger | null>(null);
  const [form] = Form.useForm<BloggerCreate>();
  const [tagDictOpen, setTagDictOpen] = useState(false);
  const [workOpen, setWorkOpen] = useState(false);
  const [claimTargets, setClaimTargets] = useState<Blogger[]>([]);
  const [selected, setSelected] = useState<Blogger[]>([]);
  const [importWorkId, setImportWorkId] = useState<string>();
  // 本页刚上传的博主导入批次，「标签字典 → 导入缺的标签」按它定位（r1 N14 改法）
  const [lastBloggerBatchId, setLastBloggerBatchId] = useState<string>();
  const user = useAuthStore((s) => s.user);
  const canEditQuote = !readOnly && Boolean(user?.roles.some((r) => QUOTE_WRITE_ROLES.includes(r)));
  const autoClaimCreate = !readOnly && Boolean(user?.roles.includes("pr"));
  const autoClaimImport = autoClaimCreate && !user?.roles.some(r => ["admin", "pr_manager"].includes(r));
  const claimSettings = useQuery({queryKey: ["blogger-claim-settings"], queryFn: getClaimSettings});
  const claimsEnabled = Boolean(claimSettings.data?.enabled_at);
  const workWechats = useQuery({queryKey: ["blogger-work-wechats"], queryFn: listWorkWechats});
  const workOptions = (workWechats.data?.items ?? []).filter(w => w.is_active).map(w => ({value: w.id, label: w.value}));
  const enable = useMutation({mutationFn: enableClaims, onSuccess: () => {
    for (const key of ["blogger-claim-settings", "bloggers"]) void qc.invalidateQueries({queryKey: [key]});
    message.success("认领规则已启用");
  }, onError: e => message.error(extractErrorMessage(e))});

  const { data, isLoading } = useQuery({
    queryKey: ["bloggers", filters],
    queryFn: () => listBloggers(filters),
  });

  // 列表筛选与编辑共用标签字典。
  const tagDictQuery = useQuery({
    queryKey: ["blogger-tags"],
    queryFn: listBloggerTags,
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
      // 悬浮看最近合作过的款式：挑博主时不用再切页去翻推广单（PRD V1.4 改动 3）
      title: "昵称",
      dataIndex: "nickname",
      width: 140,
      render: (v: string, record: Blogger) => (
        <BloggerHoverCard bloggerId={record.id} bloggerName={v} blogger={record}>
          {v}
        </BloggerHoverCard>
      ),
    },
    // 8b-1：列名历史原因叫 xiaohongshu_id，语义是平台账号（抖音 = 灰豚博主ID）
    { title: "账号", dataIndex: "xiaohongshu_id", width: 130 },
    { title: "平台", dataIndex: "platform", width: 80 },
    { title: "认领", key: "claims", width: 135, render: (_, row) => <Button type="link" size="small" onClick={() => setClaimTargets([row])}>{row.claimed_by_me ? "我已认领" : "查看 / 认领"}（{row.claim_count ?? 0}）</Button> },
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
      render: (v: string | null) => (v == null ? "—" : formatMoney(v)),
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
      title: "画像更新",
      dataIndex: "audience_updated_on",
      width: 110,
      render: (v: string | null | undefined) => v || "—",
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
      render: (v: boolean) => (v ? <Tag color="red" title="历史记录，原判定依据待核实">疑似（历史）</Tag> : "未标记"),
    },
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
      key: "actions",
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
      extra={!readOnly &&
        <Space wrap>
          <ImportUploadButton
            source="manual_blogger"
            claimWorkWechatId={importWorkId}
            requireWorkWechat={autoClaimImport && claimsEnabled}
            label="导入博主库"
            invalidateKeys={[["bloggers"], ["blogger-tags"]]}
            templateColumns={MANUAL_BLOGGER_COLUMNS}
            onUploaded={setLastBloggerBatchId}
          />
          <Button icon={<TagsOutlined />} onClick={() => setTagDictOpen(true)}>
            标签字典
          </Button>
          <Button onClick={() => setWorkOpen(true)}>工作微信</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建博主
          </Button>
        </Space>
      }
    >
      {claimSettings.data && <Alert style={{marginBottom: 12}} showIcon type={claimSettings.data.enabled_at ? "success" : "info"}
        message={claimSettings.data.enabled_at ? "认领规则已启用：联系方式与新合作范围按认领生效" : "准备分配中：当前仍沿用原权限，分配后由主管启用认领规则"}
        description={`未分配博主 ${claimSettings.data.unclaimed_count} 位；工作微信待补 ${claimSettings.data.pending_work_wechat_count} 条。既有业务单继续由原负责人处理。`}
        action={!readOnly && claimSettings.data.can_enable && !claimSettings.data.enabled_at ? <Popconfirm title="确认已完成必要分配？启用后未认领 PR 不能查看/修改联系方式或发起新合作。" onConfirm={() => enable.mutate()}><Button loading={enable.isPending}>启用认领规则</Button></Popconfirm> : undefined} />}
      {autoClaimImport && <Space style={{marginBottom: 12}} wrap><Typography.Text>本批导入认领使用：</Typography.Text><Select aria-label="导入工作微信" placeholder={claimsEnabled ? "先选工作微信，再导入" : "可选，准备期允许留空待补"} value={importWorkId} onChange={setImportWorkId} options={workOptions} style={{width: 260}} /><Typography.Text type="secondary">{claimsEnabled ? "资料与认领结果在导入记录中分别提示" : "准备期可先导入；自动认领的工作微信留待补充"}</Typography.Text></Space>}
      <Space style={{ marginBottom: 16 }} wrap>
        <Select aria-label="认领筛选" value={filters.claimed_by_me ? "mine" : "all"} style={{width: 130}} options={[{label: "全部博主", value: "all"}, {label: "我认领的", value: "mine"}]} onChange={v => setFilters(f => ({...f, claimed_by_me: v === "mine" || undefined, page: 1}))} />
        <Select aria-label="工作微信筛选" placeholder="认领工作微信" allowClear style={{width: 180}} value={filters.work_wechat_id} options={(workWechats.data?.items ?? []).map(w => ({value: w.id, label: w.value + (w.is_active ? "" : "（已停用）")}))} onChange={v => setFilters(f => ({...f, work_wechat_id: v, page: 1}))} />
        {!readOnly && <Button disabled={!selected.length} onClick={() => setClaimTargets(selected)}>批量认领 / 分配（{selected.length}）</Button>}
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
        <Select mode="multiple" allowClear aria-label="类目标签筛选" placeholder="类目标签（匹配任一）"
          value={filters.category_tags} options={categoryTagOptions} maxCount={CATEGORY_TAG_MAX}
          style={{ minWidth: 200, maxWidth: 380 }} loading={tagDictQuery.isLoading}
          onChange={(tags: string[]) => setFilters((f) => ({ ...f, category_tags: tags.length ? tags : undefined, page: 1 }))} />
        <Select mode="multiple" allowClear aria-label="系统标签筛选" placeholder="系统标签（匹配任一）"
          value={filters.quality_tags} options={(tagDictQuery.data?.system_tags ?? []).map((value) => ({ label: value, value }))}
          style={{ minWidth: 200, maxWidth: 380 }} maxCount={CATEGORY_TAG_MAX}
          onChange={(tags: string[]) => setFilters((f) => ({ ...f, quality_tags: tags.length ? tags : undefined, page: 1 }))} />
        <Select
          aria-label="近期数据筛选"
          value={filters.recent_growth_only ? "up" : "all"}
          style={{ width: 120 }}
          options={[
            { label: "全部", value: "all" },
            { label: "历史数据上涨", value: "up" },
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
        rowSelection={readOnly ? undefined : {selectedRowKeys: selected.map(r => r.id), onChange: (_, rows) => setSelected(rows)}}
        loading={isLoading}
        columns={readOnly ? columns.filter((column) => column.key !== "actions") : columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1450 }}
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
        {editing && <BloggerLegacyMetrics blogger={editing} />}
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => saveMutation.mutate(v)}
          style={{ marginTop: 16 }}
          initialValues={{ platform: "小红书" }}
        >
          {!editing && autoClaimCreate && <Form.Item name="claim_work_wechat_id" label="认领主工作微信" rules={[{required: claimsEnabled, message: "请选择认领使用的工作微信"}]} extra={claimsEnabled ? "创建成功后自动认领；没有可选账号请主管先维护工作微信。" : "准备期可留空创建，自动认领后显示工作微信待补。"}><Select options={workOptions} placeholder={claimsEnabled ? "选择工作微信" : "可选，准备期允许留空待补"} /></Form.Item>}
          <Form.Item
            name="xiaohongshu_id"
            label="账号"
            extra="同一平台下账号不能重复；已有账号继续沿用"
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
              <Input placeholder={editing?.contact_permissions?.wechat?.write === false ? "需认领及字段权限" : "微信号（可选）"} disabled={editing?.contact_permissions?.wechat?.write === false} style={{ width: 180 }} />
            </Form.Item>
          </Space>
          <Form.Item
            name="homepage_url"
            label="主页链接"
            extra="从博主主页的“分享 → 复制链接”获取，可直接粘贴分享文案"
            rules={[
              {
                validator: (_, value: string | undefined) => !value?.trim() || safeHomepageUrl(value)
                  ? Promise.resolve() : Promise.reject(new Error("请填写 http/https 主页网址，不是账号或昵称")),
              },
            ]}
          >
            <Input placeholder="https://…（可粘贴分享文案）" maxLength={1024} onPaste={(event) => {
              const url = extractHomepageUrl(event.clipboardData.getData("text"));
              if (url) {
                event.preventDefault();
                form.setFieldValue("homepage_url", url);
                message.success("已识别链接");
              }
            }} />
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
      <WorkWechatModal open={workOpen} onClose={() => setWorkOpen(false)} />
      {claimTargets.length > 0 && <BloggerClaimsModal targets={claimTargets} onClose={() => setClaimTargets([])} />}
    </Card>
  );
}
