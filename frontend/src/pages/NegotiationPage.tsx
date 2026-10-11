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
  theme,
  type MenuProps,
} from "antd";
import { DownOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  createNegotiation,
  listNegotiations,
  negotiationStatusCounts,
  submitNegotiation,
  updateNegotiation,
} from "@/features/negotiation/api";
import type {
  CooperationMode,
  Negotiation,
  NegotiationFilters,
  NegotiationStatus,
} from "@/features/negotiation/types";
import {
  NEGOTIATION_TABS,
  negotiationImages,
  negotiationItemLines,
} from "@/features/negotiation/negotiationList";
import { extractErrorMessage } from "@/services/apiClient";
import { BloggerHoverCard } from "@/components/BloggerHoverCard/BloggerHoverCard";
import { DisplayNameCell } from "@/components/DisplayNameCell/DisplayNameCell";
import { GoodsImages } from "@/components/GoodsImages/GoodsImages";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { StyleSelect } from "@/components/RemoteSelect/StyleSelect";
import { PLATFORMS } from "@/features/common/platforms";
import { FlowAction } from "@/features/flow/FlowAction";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { buildMenuItems } from "@/features/flow/flowMenu";
import type { UiAction } from "@/features/flow/keys";
import { negotiationStatusStyle } from "@/features/flow/stageStyle";
import "./NegotiationPage.css";

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

/** status-counts 里「主管待审」超 36 小时的单数（看不到超时高亮的人恒为 0）。 */
const OVERDUE_COUNT_KEY = "主管待审超时";

const ONE_LINE = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

/** 菜单里要开弹窗的流程动作；弹窗在 features/negotiation/components。 */
export type NegotiationDialog = {
  kind: "manager_review" | "final_review" | "receiver" | "copy";
  row: Negotiation;
  action: UiAction;
};

/**
 * 谈款（PRD V1.4 模块一；流程线设计 3.2、8.3）。
 *
 * PR 录入谈款并提交 → PR 主管审核 → 老板（管理员）定稿 → PR 录收货信息并确认，系统生成推广单。
 * 之后寄样、催发、召回、结款都在推广管理里走。
 *
 * 能做什么一律按接口的 `ui`：行内「操作」下拉来自 `row.ui`，「新建谈款」来自列表页级 `ui.actions.create`；
 * 页面不按角色码判断。可见范围（只看自己的 / 看全部）由后端定。
 */
export function NegotiationPage() {
  const qc = useQueryClient();
  const { token } = theme.useToken();

  const [tab, setTab] = useState<NegotiationStatus | "">("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [filters, setFilters] = useState<NegotiationFilters>({
    page: 1,
    page_size: 20,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Negotiation | null>(null);
  const [form] = Form.useForm();
  const formPlatform = Form.useWatch("platform", form) as string | undefined;
  const [, setDialog] = useState<NegotiationDialog | null>(null);

  const effectiveFilters = useMemo<NegotiationFilters>(
    () => ({
      ...filters,
      status: tab || undefined,
      overdue_only: overdueOnly || undefined,
    }),
    [filters, tab, overdueOnly]
  );

  const { data, isLoading } = useQuery({
    queryKey: ["negotiations", effectiveFilters],
    queryFn: () => listNegotiations(effectiveFilters),
  });
  const { data: counts } = useQuery({
    queryKey: ["negotiation-status-counts"],
    queryFn: negotiationStatusCounts,
  });
  const overdueCount = counts?.[OVERDUE_COUNT_KEY] ?? 0;

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["negotiations"] });
    void qc.invalidateQueries({ queryKey: ["negotiation-status-counts"] });
  }

  function showOverdue() {
    setTab("主管待审");
    setOverdueOnly(true);
    setFilters((f) => ({ ...f, page: 1 }));
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
    mutationFn: (row: Negotiation) => submitNegotiation(row.id),
    onSuccess: () => {
      message.success("已提交，等 PR 主管审核");
      invalidate();
    },
    onError: (err, row) => {
      const outcome = flowErrorOutcome(err);
      if (outcome.kind === "gate") {
        // 缺项在编辑弹窗里补
        message.warning(`缺：${outcome.missing.map((m) => m.label).join("、")}`);
        openEdit(row);
        return;
      }
      message.error(outcome.text);
      if (outcome.refresh) invalidate();
    },
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

  /** 行内「操作」：顺序与禁用原因由 buildMenuItems 按 row.ui 定；ui 里没有的项不出现。 */
  function rowMenuItems(record: Negotiation): NonNullable<MenuProps["items"]> {
    const open = (kind: NegotiationDialog["kind"]) => (action: UiAction) =>
      setDialog({ kind, row: record, action });
    const built = buildMenuItems("negotiation", record.ui, {
      actions: {
        edit: { label: "编辑", onClick: () => openEdit(record) },
        submit: {
          label: "提交审核",
          // 缺项能在编辑弹窗里补（★¹ in_dialog）：带 missing 时先开弹窗
          onClick: (action) =>
            action.missing?.length ? openEdit(record) : submitMutation.mutate(record),
        },
        manager_review: { label: "主管审核", onClick: open("manager_review") },
        final_review: { label: "老板审核", onClick: open("final_review") },
        receiver_save: { label: "录收货信息", onClick: open("receiver") },
        receiver_confirm: { label: "确认收货并生成推广单", onClick: open("receiver") },
        copy: { label: "复制为新谈款", onClick: open("copy") },
      },
    });
    // FlowMenuItem 是 interface（没有 antd MenuItemType 的 data-* 索引签名），逐项转成字面量
    return built.map(({ key, label, disabled, danger, onClick }) => ({
      key,
      label,
      disabled,
      danger,
      onClick,
    }));
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

  const rows = data?.items ?? [];
  const menus = new Map(rows.map((r) => [r.id, rowMenuItems(r)]));
  // 本页所有行都没有可做的动作（财务、运营）时整列不显示（8.2）
  const showOperations = [...menus.values()].some((items) => items.length > 0);

  const columns: ColumnsType<Negotiation> = [
    {
      title: "主图",
      key: "images",
      width: 148,
      fixed: "left",
      render: (_, row) => (
        <GoodsImages
          images={negotiationImages(row)}
          displayName={row.display_short_name ?? row.style_name ?? "谈款商品"}
          size={64}
        />
      ),
    },
    {
      title: "博主",
      dataIndex: "blogger_nickname",
      width: 140,
      fixed: "left",
      render: (name: string | null, row) => (
        <BloggerHoverCard bloggerId={row.blogger_id} bloggerName={name}>
          {name ?? "—"}
        </BloggerHoverCard>
      ),
    },
    {
      // 商品简称（没填回落款式简称 / 款式名），悬停看全称；下面每个成员一行颜色尺码。不显示编码（规-1）
      title: "商品",
      dataIndex: "display_short_name",
      width: 200,
      render: (v: string | null, row) => (
        <div style={{ minWidth: 0 }}>
          <Space size={4} wrap>
            <DisplayNameCell name={v ?? row.style_name} fullTitle={row.goods_title} />
            {row.goods_is_suit && <Tag color="purple">套装</Tag>}
          </Space>
          {negotiationItemLines(row).map((line, i) => (
            <div
              key={i}
              title={line}
              style={{ ...ONE_LINE, color: token.colorTextSecondary, fontSize: token.fontSizeSM }}
            >
              {line}
            </div>
          ))}
        </div>
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
      width: 170,
      render: (v: NegotiationStatus, row) => (
        <Space size={4} direction="vertical" align="start">
          <Space size={4} wrap>
            <Tag color={negotiationStatusStyle(v).color}>{negotiationStatusStyle(v).label}</Tag>
            {row.overdue && (
              <Tag
                color="red"
                title={row.overdue_hours != null ? `已等 ${row.overdue_hours} 小时` : undefined}
              >
                超 36 小时
              </Tag>
            )}
          </Space>
          {v === "审核驳回" && row.review_opinion && (
            <Tooltip title={row.review_opinion}>
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                驳回原因
              </Typography.Text>
            </Tooltip>
          )}
          {v === "已定稿" && row.promotion_internal_code && (
            <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>
              推广单 {row.promotion_internal_code}
            </span>
          )}
        </Space>
      ),
    },
  ];
  if (showOperations) {
    columns.push({
      title: "操作",
      key: "operations",
      width: 100,
      fixed: "right",
      render: (_, record) => {
        const items = menus.get(record.id) ?? [];
        if (!items.length) return null;
        return (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="link" size="small">
              操作 <DownOutlined />
            </Button>
          </Dropdown>
        );
      },
    });
  }

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          谈款审核
        </Typography.Title>
      }
      extra={
        <FlowAction
          ui={data?.ui}
          actionKey="create"
          label="新建谈款"
          onClick={openCreate}
          showReasonBelow
          buttonProps={{ type: "primary", icon: <PlusOutlined /> }}
        />
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        PR 录入谈款并提交，PR 主管审核、老板定稿后录收货信息，确认后系统生成推广单。之后寄样、催发、召回、
        结款都在推广管理里走。
      </Typography.Paragraph>

      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k as NegotiationStatus | "");
          setOverdueOnly(false);
          setFilters((f) => ({ ...f, page: 1 }));
        }}
        items={NEGOTIATION_TABS.map((t) => ({
          key: t.key,
          label: t.key ? (
            <Space size={4}>
              <Badge count={counts?.[t.key] ?? 0} offset={[10, 0]} size="small">
                <span style={{ paddingRight: 12 }}>{t.label}</span>
              </Badge>
              {t.key === "主管待审" && overdueCount > 0 && (
                <Tag
                  color="red"
                  role="button"
                  tabIndex={0}
                  aria-pressed={overdueOnly}
                  aria-label={`只看超 36 小时的主管待审（${overdueCount} 条）`}
                  style={{ cursor: "pointer", marginInlineEnd: 0 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    showOverdue();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      showOverdue();
                    }
                  }}
                >
                  超时 {overdueCount}
                </Tag>
              )}
            </Space>
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
        {overdueOnly && (
          <Tag color="red" closable onClose={() => setOverdueOnly(false)}>
            只看主管待审超 36 小时
          </Tag>
        )}
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        rowClassName={(r) => (r.overdue ? "negotiation-row-overdue" : "")}
        scroll={{ x: 1300 }}
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
    </Card>
  );
}
