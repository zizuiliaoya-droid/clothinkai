import { useMemo, useState } from "react";
import {
  Badge,
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
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { DownOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  createNegotiation,
  listNegotiations,
  negotiationStatusCounts,
  reviewNegotiation,
  submitNegotiation,
  updateNegotiation,
} from "@/features/negotiation/api";
import type {
  CooperationMode,
  Negotiation,
  NegotiationFilters,
  NegotiationStatus,
} from "@/features/negotiation/types";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { BloggerHoverCard } from "@/components/BloggerHoverCard/BloggerHoverCard";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { StyleSelect } from "@/components/RemoteSelect/StyleSelect";
import { PLATFORMS } from "@/features/common/platforms";

const MODES: CooperationMode[] = ["寄拍", "送拍", "置换"];

const MODE_HINT: Record<CooperationMode, string> = {
  寄拍: "衣服要寄回，样品成本记 0，只有寄回运费计入成本",
  送拍: "衣服送给博主，样品成本取商品成员款式的货品成本之和",
  置换: "以货换推广，没有博主服务费",
};

const modeColor: Record<string, string> = {
  寄拍: "blue",
  送拍: "green",
  置换: "purple",
};

const statusColor: Record<NegotiationStatus, string> = {
  草稿: "default",
  待审核: "gold",
  审核通过: "green",
  审核驳回: "red",
};

const TABS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "待审核", label: "待审核" },
  { key: "草稿", label: "草稿" },
  { key: "审核驳回", label: "已驳回" },
  { key: "审核通过", label: "已通过" },
];

/**
 * 谈款审核（PRD V1.4 模块一）。
 *
 * PR 录入谈款信息落草稿 → 提交审核 → 主管通过后系统自动生成推广单。之后所有执行
 * （寄样、催发、召回、结款）都在推广管理里走，谈款单只留作审批记录。
 *
 * 权限：PR 能建能改能提交但不能审；主管能审；财务只读。后端 scope 用独立一级域
 * negotiation，所以 PR 的 promotion.*:* 命中不了审核权。
 */
export function NegotiationPage() {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const canReview = Boolean(
    user?.roles.some((r) => ["admin", "platform_admin", "pr_manager"].includes(r))
  );

  const [tab, setTab] = useState("");
  const [filters, setFilters] = useState<NegotiationFilters>({
    page: 1,
    page_size: 20,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Negotiation | null>(null);
  const [form] = Form.useForm();
  const formPlatform = Form.useWatch("platform", form) as string | undefined;
  const [rejectTarget, setRejectTarget] = useState<Negotiation | null>(null);
  const [rejectForm] = Form.useForm();

  const effectiveFilters = useMemo<NegotiationFilters>(
    () => ({ ...filters, status: (tab || undefined) as NegotiationStatus | undefined }),
    [filters, tab]
  );

  const { data, isLoading } = useQuery({
    queryKey: ["negotiations", effectiveFilters],
    queryFn: () => listNegotiations(effectiveFilters),
  });
  const { data: counts } = useQuery({
    queryKey: ["negotiation-status-counts"],
    queryFn: negotiationStatusCounts,
  });
  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["negotiations"] });
    void qc.invalidateQueries({ queryKey: ["negotiation-status-counts"] });
  }

  const saveMutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const payload = {
        blogger_id: values.blogger_id as string,
        style_id: values.style_id as string,
        cooperation_mode: values.cooperation_mode as CooperationMode,
        platform: values.platform as string,
        scheduled_publish_date: values.scheduled_publish_date
          ? dayjs(values.scheduled_publish_date as dayjs.Dayjs).format("YYYY-MM-DD")
          : null,
        quote_amount:
          values.quote_amount != null ? String(values.quote_amount) : null,
        remark: (values.remark as string) || null,
      };
      if (editing) return updateNegotiation(editing.id, payload);
      return createNegotiation(payload);
    },
    onSuccess: (saved) => {
      message.success(
        saved.cooperation_mode === "置换"
          ? "已保存。置换没有博主服务费，报价已置 0"
          : "已保存"
      );
      closeForm();
      invalidate();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const submitMutation = useMutation({
    mutationFn: (id: string) => submitNegotiation(id),
    onSuccess: () => {
      message.success("已提交审核");
      invalidate();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const approveMutation = useMutation({
    mutationFn: (id: string) => reviewNegotiation(id, { action: "approve" }),
    onSuccess: (saved) => {
      message.success(
        saved.promotion_internal_code
          ? `审核通过，已生成推广单 ${saved.promotion_internal_code}`
          : "审核通过"
      );
      invalidate();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, opinion }: { id: string; opinion: string }) =>
      reviewNegotiation(id, { action: "reject", review_opinion: opinion }),
    onSuccess: () => {
      message.success("已驳回，PR 可以修改后重新提交");
      setRejectTarget(null);
      rejectForm.resetFields();
      invalidate();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeForm() {
    setFormOpen(false);
    setEditing(null);
    form.resetFields();
  }

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ platform: "小红书" });
    setFormOpen(true);
  }

  function openEdit(record: Negotiation) {
    setEditing(record);
    form.resetFields();
    form.setFieldsValue({
      blogger_id: record.blogger_id,
      style_id: record.style_id,
      cooperation_mode: record.cooperation_mode,
      platform: record.platform,
      scheduled_publish_date: record.scheduled_publish_date
        ? dayjs(record.scheduled_publish_date)
        : undefined,
      quote_amount:
        record.quote_amount != null ? Number(record.quote_amount) : undefined,
      remark: record.remark ?? undefined,
    });
    setFormOpen(true);
  }

  // 编辑时已选的博主 / 款式多半不在搜索结果的前 20 条里，交给下拉回显，否则只显示 UUID
  const bloggerEcho = editing
    ? { value: editing.blogger_id, label: editing.blogger_nickname || editing.blogger_id }
    : null;
  const styleEcho = editing
    ? {
        value: editing.style_id,
        label:
          `${editing.style_code ?? ""} ${editing.style_name ?? ""}`.trim() ||
          editing.style_id,
      }
    : null;

  const columns: ColumnsType<Negotiation> = [
    {
      title: "博主",
      dataIndex: "blogger_nickname",
      width: 150,
      fixed: "left",
      render: (name: string | null, row) => (
        <BloggerHoverCard bloggerId={row.blogger_id} bloggerName={name}>
          {name ?? "—"}
        </BloggerHoverCard>
      ),
    },
    {
      title: "款式",
      dataIndex: "style_code",
      width: 180,
      render: (code: string | null, row) =>
        code ? (
          <span>
            {code}
            {row.style_name && (
              <Typography.Text type="secondary" style={{ marginLeft: 6 }}>
                {row.style_name}
              </Typography.Text>
            )}
          </span>
        ) : (
          "—"
        ),
    },
    {
      title: "合作模式",
      dataIndex: "cooperation_mode",
      width: 100,
      render: (v: CooperationMode) => (
        <Tooltip title={MODE_HINT[v]}>
          <Tag color={modeColor[v]}>{v}</Tag>
        </Tooltip>
      ),
    },
    { title: "平台", dataIndex: "platform", width: 90 },
    {
      title: "约定发布",
      dataIndex: "scheduled_publish_date",
      width: 110,
      render: (v: string | null) => v || "—",
    },
    {
      title: "服务费",
      dataIndex: "quote_amount",
      width: 100,
      align: "right",
      render: (v: string | null) => (v == null ? "—" : `¥${Number(v).toFixed(2)}`),
    },
    { title: "对接PR", dataIndex: "pr_name", width: 100, render: (v) => v || "—" },
    {
      title: "状态",
      dataIndex: "status",
      width: 150,
      render: (v: NegotiationStatus, row) => (
        <Space size={4} direction="vertical" align="start">
          <Tag color={statusColor[v]}>{v}</Tag>
          {v === "审核驳回" && row.review_opinion && (
            <Tooltip title={row.review_opinion}>
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                驳回原因
              </Typography.Text>
            </Tooltip>
          )}
          {v === "审核通过" && row.promotion_internal_code && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              推广单 {row.promotion_internal_code}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: "操作",
      width: 110,
      fixed: "right",
      render: (_, record) => {
        const editable = ["草稿", "审核驳回"].includes(record.status);
        const reviewable = record.status === "待审核" && canReview;
        const items = [
          {
            key: "edit",
            label: "编辑",
            disabled: !editable,
            onClick: () => openEdit(record),
          },
          {
            key: "submit",
            label: "提交审核",
            disabled: !editable,
            onClick: () => submitMutation.mutate(record.id),
          },
          {
            key: "approve",
            label: "审核通过",
            disabled: !reviewable,
            onClick: () => approveMutation.mutate(record.id),
          },
          {
            key: "reject",
            label: "审核驳回",
            danger: true,
            disabled: !reviewable,
            onClick: () => {
              setRejectTarget(record);
              rejectForm.resetFields();
            },
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
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          谈款审核
        </Typography.Title>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建谈款
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        PR 录入谈款信息并提交，主管审核通过后系统自动生成推广单。之后寄样、催发、召回、
        结款都在推广管理里走，这里只留审批记录。
      </Typography.Paragraph>

      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k);
          setFilters((f) => ({ ...f, page: 1 }));
        }}
        items={TABS.map((t) => ({
          key: t.key,
          label: t.key ? (
            <Badge count={counts?.[t.key] ?? 0} offset={[10, 0]} size="small">
              <span style={{ paddingRight: 12 }}>{t.label}</span>
            </Badge>
          ) : (
            t.label
          ),
        }))}
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="博主昵称 / 款号 / 款名"
          allowClear
          style={{ width: 240 }}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="合作模式"
          allowClear
          style={{ width: 120 }}
          options={MODES.map((m) => ({ label: m, value: m }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, cooperation_mode: v, page: 1 }))
          }
        />
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1200 }}
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
        title={editing ? "编辑谈款" : "新建谈款"}
        open={formOpen}
        onCancel={closeForm}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={600}
      >
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v) => saveMutation.mutate(v)}
        >
          <Form.Item
            name="blogger_id"
            label="博主"
            rules={[{ required: true, message: "请选择博主" }]}
          >
            <BloggerSelect selected={bloggerEcho} platform={formPlatform} />
          </Form.Item>
          <Form.Item
            name="style_id"
            label="款式"
            rules={[{ required: true, message: "请选择款式" }]}
          >
            <StyleSelect selected={styleEcho} />
          </Form.Item>
          <Form.Item
            name="cooperation_mode"
            label="合作模式"
            rules={[{ required: true, message: "请选择合作模式" }]}
            tooltip="决定样品成本与博主服务费怎么算。生成推广单后就锁定了。"
          >
            <Select
              placeholder="选择合作模式"
              options={MODES.map((m) => ({
                label: `${m} · ${MODE_HINT[m]}`,
                value: m,
              }))}
            />
          </Form.Item>
          <Space align="start" style={{ display: "flex" }}>
            <Form.Item
              name="platform"
              label="平台"
              rules={[{ required: true }]}
              style={{ width: 160 }}
            >
              <Select options={PLATFORMS.map((p) => ({ label: p, value: p }))} />
            </Form.Item>
            <Form.Item
              name="scheduled_publish_date"
              label="约定发布时间"
              style={{ width: 180 }}
            >
              <DatePicker style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="quote_amount"
              label="博主服务费"
              tooltip="不填则取博主档案里的报价。置换模式填了也会被置 0。"
              style={{ width: 180 }}
            >
              <InputNumber
                min={0}
                precision={2}
                style={{ width: "100%" }}
                addonBefore="¥"
              />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="驳回谈款"
        open={!!rejectTarget}
        onCancel={() => setRejectTarget(null)}
        onOk={() => rejectForm.submit()}
        confirmLoading={rejectMutation.isPending}
        okButtonProps={{ danger: true }}
        okText="确认驳回"
        destroyOnHidden
      >
        <Form
          form={rejectForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v: { review_opinion: string }) => {
            if (!rejectTarget) return;
            rejectMutation.mutate({
              id: rejectTarget.id,
              opinion: v.review_opinion,
            });
          }}
        >
          <Form.Item
            name="review_opinion"
            label="审核意见"
            rules={[{ required: true, message: "请填写审核意见" }]}
            tooltip="PR 会看到这段文字，要说清楚需要改什么。"
          >
            <Input.TextArea rows={3} placeholder="如：报价偏高，再谈一轮" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
