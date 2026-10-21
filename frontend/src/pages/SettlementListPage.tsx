import { useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Dropdown,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  theme,
  type MenuProps,
} from "antd";
import { DownOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { addExtraItem, listSettlements, reviewSettlement } from "@/features/finance/api";
import { PaymentAmountModal } from "@/features/finance/components/PaymentAmountModal";
import { PaymentProofModal } from "@/features/finance/components/PaymentProofModal";
import type {
  ExtraItemType,
  Settlement,
  SettlementListFilters,
  SettlementStatus,
} from "@/features/finance/types";
import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { flowErrorOutcome } from "@/features/flow/flowError";
import { buildMenuItems } from "@/features/flow/flowMenu";
import { LineDrawer } from "@/features/flow/LineDrawer";
import { DisplayNameCell } from "@/components/DisplayNameCell/DisplayNameCell";
import { ImportUploadButton } from "@/components/ImportUploadButton";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const STATUS: SettlementStatus[] = [
  "待核查",
  "待付款",
  "待财务付款",
  "已付款",
  "已驳回",
];
/** 筛选项的文案：新单不再进待核查，这一档只剩上线前的旧单（8.6）。 */
const STATUS_LABEL: Partial<Record<SettlementStatus, string>> = { 待核查: "待核查（旧单）" };
const statusColor: Record<string, string> = {
  待核查: "orange",
  待付款: "blue",
  待财务付款: "cyan",
  已付款: "green",
  已驳回: "red",
};
// 召回寄回运费走 R33；历史运费类型保留在响应中，新增不再提供。
const EXTRA_ITEM_TYPES: ExtraItemType[] = ["赞奖", "其他"];

/**
 * 财务结款（流程线 8.6）。列对齐 final.xlsx「站外结款表」，另加推广编号（点开主线抽屉）、合作模式、应付、实付、
 * 调整说明、收款码；「单据信息」用行展开（发布链接、推广备注、推广驳回原因、重提说明），不显示收件与发货信息（Q4）。
 * 行内操作按接口的行级 `ui.actions`：核查通过（只给上线前的旧单）、驳回、增加结算项、填付款金额、上传付款凭证。
 */
export function SettlementListPage() {
  const qc = useQueryClient();
  const { token } = theme.useToken();
  const [filters, setFilters] = useState<SettlementListFilters>({
    page: 1,
    page_size: 20,
  });

  // 弹窗状态
  const [target, setTarget] = useState<Settlement | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [extraOpen, setExtraOpen] = useState(false);
  const [amountTarget, setAmountTarget] = useState<Settlement | null>(null);
  const [proofTarget, setProofTarget] = useState<Settlement | null>(null);
  const [lineRow, setLineRow] = useState<Settlement | null>(null);
  const [rejectForm] = Form.useForm();
  const [extraForm] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ["settlements", filters],
    queryFn: () => listSettlements(filters),
  });
  // 「导入结款」只看页级 ui.actions.import（流程线 PR-5）：财务、管理员有，主管没有
  const canImport = data?.ui?.actions?.import?.state === "enabled";

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["settlements"] });
    void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL });
  };
  /** 8.2：409 / 权限刚变 → 统一文案并刷新（重新拿 ui）；其余显示接口的提示。 */
  const onFlowError = (err: unknown) => {
    const outcome = flowErrorOutcome(err);
    if (outcome.kind === "gate") {
      message.error(`缺：${outcome.missing.map((m) => m.label).join("、")}`);
      return false;
    }
    message.error(outcome.text);
    if (outcome.refresh) refresh();
    return outcome.refresh;
  };

  const approveMutation = useMutation({
    mutationFn: (id: string) => reviewSettlement(id, { action: "approve" }),
    onSuccess: () => {
      message.success("已核查通过");
      refresh();
    },
    onError: onFlowError,
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      reviewSettlement(id, { action: "reject", review_reason: reason }),
    onSuccess: () => {
      message.success("已驳回，推广单转「结款驳回」");
      setRejectOpen(false);
      refresh();
    },
    onError: (err) => {
      if (onFlowError(err)) setRejectOpen(false);
    },
  });

  const extraMutation = useMutation({
    mutationFn: ({
      id,
      item_type,
      amount,
      remark,
    }: {
      id: string;
      item_type: ExtraItemType;
      amount: string;
      remark?: string;
    }) => addExtraItem(id, { item_type, amount, remark: remark || null }),
    onSuccess: () => {
      message.success("结算项已添加");
      setExtraOpen(false);
      refresh();
    },
    onError: (err) => {
      if (onFlowError(err)) setExtraOpen(false);
    },
  });

  function openReject(r: Settlement) {
    setTarget(r);
    rejectForm.resetFields();
    setRejectOpen(true);
  }
  function openExtra(r: Settlement) {
    setTarget(r);
    extraForm.resetFields();
    setExtraOpen(true);
  }

  /** 行内「操作」：顺序与禁用原因由 buildMenuItems 按 row.ui 定；ui 里没有的项不出现。 */
  function rowMenuItems(record: Settlement): NonNullable<MenuProps["items"]> {
    const built = buildMenuItems("settlement", record.ui, {
      actions: {
        legacy_approve: { label: "核查通过", onClick: () => approveMutation.mutate(record.id) },
        reject: { label: "驳回", danger: true, onClick: () => openReject(record) },
        extra_item: { label: "增加结算项", onClick: () => openExtra(record) },
        fill_payment: { label: "填付款金额", onClick: () => setAmountTarget(record) },
        upload_proof: { label: "上传付款凭证", onClick: () => setProofTarget(record) },
      },
    });
    return built.map(({ key, label, disabled, danger, onClick }) => ({
      key,
      label,
      disabled,
      danger,
      onClick,
    }));
  }

  const rows = data?.items ?? [];
  const menus = new Map(rows.map((r) => [r.id, rowMenuItems(r)]));
  // 本页所有行都没有可做的动作时整列不显示（8.2）
  const showOperations = [...menus.values()].some((items) => items.length > 0);
  const dash = <span style={{ color: token.colorTextSecondary }}>—</span>;

  const columns: ColumnsType<Settlement> = [
    {
      title: "推广编号",
      dataIndex: "promotion_internal_code",
      width: 150,
      fixed: "left",
      render: (v: string | null, r) =>
        v && r.line_id ? (
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setLineRow(r)}>
            {v}
          </Button>
        ) : (
          v || "—"
        ),
    },
    {
      title: "月份",
      width: 90,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 7) || "—",
    },
    {
      title: "日期",
      width: 110,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 10) || "—",
    },
    { title: "大类", width: 90, render: () => "站外推广" },
    { title: "项目", width: 80, render: () => "佣金" },
    { title: "货号", dataIndex: "style_code", width: 120, render: (v) => v || "—" },
    {
      title: "款式",
      dataIndex: "display_short_name",
      width: 140,
      ellipsis: { showTitle: false },
      render: (v: string | null, r) => (
        <DisplayNameCell name={v ?? r.style_name} fullTitle={r.style_name} />
      ),
    },
    { title: "博主名", dataIndex: "blogger_nickname", width: 120, render: (v) => v || "—" },
    { title: "合作模式", dataIndex: "cooperation_mode", width: 90, render: (v) => v || "—" },
    { title: "结算单号", dataIndex: "settlement_no", width: 150 },
    { title: "金额", dataIndex: "amount", width: 100, render: money },
    { title: "应付", dataIndex: "total_amount", width: 100, render: money },
    { title: "实付", dataIndex: "payment_amount", width: 100, render: money },
    {
      title: "调整说明",
      dataIndex: "payment_adjust_note",
      width: 160,
      ellipsis: { showTitle: false },
      render: (v: string | null) =>
        v ? (
          <Tooltip title={v} placement="topLeft">
            <span>{v}</span>
          </Tooltip>
        ) : (
          dash
        ),
    },
    {
      title: "付款日期",
      dataIndex: "payment_date",
      width: 110,
      render: (v) => v || "—",
    },
    {
      title: "收款码",
      dataIndex: "payment_qr_signed_url",
      width: 80,
      render: (v: string | null) =>
        v ? <Image src={v} alt="收款码" width={40} height={40} style={{ objectFit: "cover" }} /> : dash,
    },
    {
      title: "付款图片",
      width: 90,
      render: (_, r) =>
        r.payment_proof_signed_url ? (
          <a href={r.payment_proof_signed_url} target="_blank" rel="noreferrer">
            查看
          </a>
        ) : (
          "—"
        ),
    },
    {
      title: "结算状态",
      dataIndex: "settlement_status",
      width: 110,
      render: (v: string) => <Tag color={statusColor[v]}>{v}</Tag>,
    },
    { title: "备注", dataIndex: "remark", width: 140, render: (v) => v || "—" },
    ...(showOperations
      ? [
          {
            title: "操作",
            key: "operations",
            width: 110,
            fixed: "right" as const,
            render: (_: unknown, record: Settlement) => {
              const items = menus.get(record.id) ?? [];
              if (items.length === 0) return null;
              return (
                <Dropdown menu={{ items }} trigger={["click"]}>
                  <Button type="link" size="small">
                    操作 <DownOutlined />
                  </Button>
                </Dropdown>
              );
            },
          },
        ]
      : []),
  ];

  /** 单据信息（7d-3）：推广单的发布链接、备注、推广驳回原因、重提说明；不含收件与发货。 */
  function docInfo(r: Settlement) {
    return (
      <Descriptions
        size="small"
        column={1}
        title="单据信息"
        items={[
          {
            key: "publish_url",
            label: "发布链接",
            children: r.publish_url ? (
              <a href={r.publish_url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>
                {r.publish_url}
              </a>
            ) : (
              dash
            ),
          },
          { key: "remark", label: "推广备注", children: r.promotion_remark || dash },
          { key: "review_reason", label: "推广驳回原因", children: r.promotion_review_reason || dash },
          { key: "resubmit_note", label: "重提说明", children: r.promotion_resubmit_note || dash },
        ]}
      />
    );
  }

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          财务结款
        </Typography.Title>
      }
      extra={
        canImport ? (
          <ImportUploadButton
            source="manual_settlement"
            label="导入结款"
            invalidateKeys={[["settlements"]]}
            successHint="结果在「系统管理 → 导入记录」里看"
          />
        ) : undefined
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="结算状态"
          aria-label="结算状态"
          allowClear
          style={{ width: 160 }}
          options={STATUS.map((s) => ({ label: STATUS_LABEL[s] ?? s, value: s }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, settlement_status: v, page: 1 }))
          }
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        expandable={{ expandedRowRender: docInfo }}
        scroll={{ x: 2200 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <LineDrawer
        lineId={lineRow?.line_id ?? null}
        onClose={() => setLineRow(null)}
        goodsName={lineRow ? lineRow.display_short_name ?? lineRow.style_name : null}
        bloggerName={lineRow?.blogger_nickname}
      />

      <Modal
        title="驳回结算"
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={() => rejectForm.submit()}
        confirmLoading={rejectMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={rejectForm}
          layout="vertical"
          onFinish={(v) =>
            target && rejectMutation.mutate({ id: target.id, reason: v.reason.trim() })
          }
        >
          <Form.Item
            name="reason"
            label="驳回原因"
            rules={[
              { required: true, whitespace: true, message: "请输入驳回原因" },
              { max: 2000, message: "驳回原因最多 2000 字" },
            ]}
            extra="驳回后推广单转「结款驳回」，PR 改好后重提"
          >
            <Input.TextArea rows={3} maxLength={2000} showCount placeholder="请填写驳回原因" />
          </Form.Item>
        </Form>
      </Modal>

      <PaymentAmountModal target={amountTarget} onClose={() => setAmountTarget(null)} onChanged={refresh} />

      <Modal
        title="增加结算项"
        open={extraOpen}
        onCancel={() => setExtraOpen(false)}
        onOk={() => extraForm.submit()}
        confirmLoading={extraMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={extraForm}
          layout="vertical"
          initialValues={{ item_type: "其他" }}
          onFinish={(v) =>
            target &&
            extraMutation.mutate({
              id: target.id,
              item_type: v.item_type,
              amount: String(v.amount),
              remark: v.remark,
            })
          }
        >
          <Form.Item name="item_type" label="类型" rules={[{ required: true }]}>
            <Select
              options={EXTRA_ITEM_TYPES.map((t) => ({ label: t, value: t }))}
            />
          </Form.Item>
          <Form.Item
            name="amount"
            label="金额"
            rules={[{ required: true, message: "请输入金额" }]}
          >
            <InputNumber
              min={0}
              precision={2}
              style={{ width: "100%" }}
              prefix="¥"
            />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <PaymentProofModal target={proofTarget} onClose={() => setProofTarget(null)} onChanged={refresh} />
    </Card>
  );
}
