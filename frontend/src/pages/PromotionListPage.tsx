import { useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Dropdown,
  Input,
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
import { DownOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { listPromotions, reviewPromotion, shipInclude } from "@/features/promotion/api";
import { buildMenuItems } from "@/features/flow/flowMenu";
import type {
  Promotion,
  PromotionListFilters,
  RetroStatus,
  ShipStatusFilter,
} from "@/features/promotion/types";
import { itemSpecLines, shipDetailLines } from "@/features/promotion/shipDisplay";
import { shipStatusStyle } from "@/features/flow/stageStyle";
import { goodsDisplayName } from "@/features/product/api";
import {
  COOPERATION_MODE_HINT,
  SOURCE_FIELD_NAMES,
  SOURCE_LIST_COLUMNS,
  recallColor,
} from "@/features/promotion/listConstants";
import { AmountLogPanel } from "@/features/promotion/components/AmountLogPanel";
import { CancelPromotionModal } from "@/features/promotion/components/CancelPromotionModal";
import { ChangeGoodsModal } from "@/features/promotion/components/ChangeGoodsModal";
import { CreatePromotionModal } from "@/features/promotion/components/CreatePromotionModal";
import { MetricsModal } from "@/features/promotion/components/MetricsModal";
import { PublishModal } from "@/features/promotion/components/PublishModal";
import { RecallModal } from "@/features/promotion/components/RecallModal";
import { RejectModal } from "@/features/promotion/components/RejectModal";
import { ResubmitModal } from "@/features/promotion/components/ResubmitModal";
import { RetroConfirmModal } from "@/features/promotion/components/RetroConfirmModal";
import { RetroModal } from "@/features/promotion/components/RetroModal";
import { ReturnWaybillModal } from "@/features/promotion/components/ReturnWaybillModal";
import { SourceExtraModal } from "@/features/promotion/components/SourceExtraModal";
import { ItemsModal } from "@/features/promotion/components/ItemsModal";
import { ReceiverModal } from "@/features/promotion/components/ReceiverModal";
import {
  ShipPushModal,
  type ShipPushTarget,
} from "@/features/promotion/components/ShipPushModal";
import { ShipWithdrawModal } from "@/features/promotion/components/ShipWithdrawModal";
import { useFlowFeedback } from "@/features/promotion/components/useFlowFeedback";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { DisplayNameCell } from "@/components/DisplayNameCell/DisplayNameCell";
import { UrgeModal } from "@/components/UrgeModal/UrgeModal";
import { PLATFORMS } from "@/features/common/platforms";

const PUBLISH_STATUS = ["未发布", "已发布", "已取消", "异常", "已删除"];

/** 发货筛选（7.3）：「待发货」只回待推送仓库那批；未进发货流程 = 历史单（none）。 */
const SHIP_FILTER_OPTIONS: { label: string; value: ShipStatusFilter }[] = [
  { label: "待发货", value: "待发货" },
  { label: "待打单", value: "待打单" },
  { label: "已发货", value: "已发货" },
  { label: "未进发货流程", value: "none" },
];

const ONE_LINE = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

const cooperationModeColor: Record<string, string> = {
  寄拍: "blue",
  送拍: "green",
  置换: "purple",
};

const settlementColor: Record<string, string> = {
  未核查: "default",
  待核查: "gold",
  待付款: "blue",
  已付款: "green",
  已驳回: "red",
};

/** 复盘状态配色（PRD 改动 4）。「未开始」不上色，由渲染逻辑决定显示什么。 */
const retroColor: Record<string, string> = {
  待复盘: "gold",
  待确认: "processing",
  已完成: "green",
};

const statusColor: Record<string, string> = {
  未发布: "default",
  已发布: "green",
  已取消: "orange",
  异常: "red",
  已删除: "red",
};

export function PromotionListPage() {
  const qc = useQueryClient();
  const { token } = theme.useToken();
  const user = useAuthStore((s) => s.user);
  const canManagePaymentQr = Boolean(
    user?.roles.some((r) => ["admin", "platform_admin", "pr", "pr_manager"].includes(r))
  );
  const [filters, setFilters] = useState<PromotionListFilters>({
    page: 1,
    page_size: 10,
  });
  // 各弹窗的表单与提交都在 features/promotion/components/ 里，页面只管开哪个、对哪条单
  const [open, setOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<Promotion | null>(null);
  const [extraTarget, setExtraTarget] = useState<Promotion | null>(null);
  const [goodsTarget, setGoodsTarget] = useState<Promotion | null>(null);
  // 取消 / 驳回 / 寄回单号 / 召回：都要填东西，各自一个弹窗。
  // 以前取消是硬编码「手动取消」直接提交，驳回则压根不传原因（后端必定 422）。
  const [cancelTarget, setCancelTarget] = useState<Promotion | null>(null);
  const [rejectTarget, setRejectTarget] = useState<Promotion | null>(null);
  const [resubmitTarget, setResubmitTarget] = useState<Promotion | null>(null);
  const [waybillTarget, setWaybillTarget] = useState<Promotion | null>(null);
  const [urgeTarget, setUrgeTarget] = useState<Promotion | null>(null);
  const [metricsTarget, setMetricsTarget] = useState<Promotion | null>(null);
  const [retroTarget, setRetroTarget] = useState<Promotion | null>(null);
  const [retroConfirmTarget, setRetroConfirmTarget] = useState<Promotion | null>(
    null
  );
  const [amountLogTarget, setAmountLogTarget] = useState<Promotion | null>(null);
  const [recallTarget, setRecallTarget] = useState<Promotion | null>(null);
  // 发货（流程线 8.4）：按行 ui 出菜单项，弹窗在 features/promotion/components/
  const [shipPushTarget, setShipPushTarget] = useState<ShipPushTarget | null>(null);
  const [shipWithdrawTarget, setShipWithdrawTarget] = useState<Promotion | null>(null);
  const [receiverTarget, setReceiverTarget] = useState<Promotion | null>(null);
  const [itemsTarget, setItemsTarget] = useState<Promotion | null>(null);
  const flowFeedback = useFlowFeedback();

  const { data, isLoading } = useQuery({
    queryKey: ["promotions", filters],
    queryFn: () => listPromotions(filters),
  });

  /** 审核通过。寄拍没有寄回单号时后端会拒，错误信息直接透出给操作人。 */
  const approveMutation = useMutation({
    mutationFn: (id: string) => reviewPromotion(id, { action: "approve" }),
    onSuccess: (saved) => {
      message.success(
        saved.settlement_status === "已付款"
          ? "审核通过。置换无需付款，已直接结清"
          : "审核通过，已转待财务付款"
      );
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  /** 纳入发货：历史单 → 待发货（之后才出「确认推送仓库」）。 */
  const shipIncludeMutation = useMutation({
    mutationFn: (id: string) => shipInclude(id),
    onSuccess: () => {
      message.success("已纳入发货，进入待推送仓库");
      flowFeedback.refresh();
    },
    onError: (err) => flowFeedback.handleError(err),
  });

  /** 发货 5 项：顺序与禁用原因由 buildMenuItems 按 row.ui 定；没有的项不出现。 */
  function shipMenuItems(record: Promotion): NonNullable<MenuProps["items"]> {
    const built = buildMenuItems("promotion", record.ui, {
      actions: {
        ship_push: {
          label: "确认推送仓库",
          onClick: (action) => setShipPushTarget({ row: record, action }),
        },
        ship_withdraw: {
          label: "撤回推送",
          danger: true,
          onClick: () => setShipWithdrawTarget(record),
        },
        ship_include: {
          label: "纳入发货",
          onClick: () =>
            Modal.confirm({
              title: `纳入发货 · ${record.internal_code}`,
              content: "纳入后这张单进入「待推送仓库」，由管理员或 PR 主管确认推送。",
              okText: "纳入发货",
              cancelText: "取消",
              onOk: () => shipIncludeMutation.mutateAsync(record.id).catch(() => undefined),
            }),
        },
      },
      edits: {
        receiver: { label: "改收件信息", onClick: () => setReceiverTarget(record) },
        goods_items: { label: "改颜色尺码", onClick: () => setItemsTarget(record) },
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

  const columns: ColumnsType<Promotion> = [
    { title: "内部编码", dataIndex: "internal_code", width: 150, fixed: "left" },
    {
      title: "主图",
      dataIndex: "style_main_image_url",
      width: 68,
      fixed: "left",
      render: (src: string | null, row) => (
        <StyleImageThumbnail src={src} alt={`${row.style_code_snapshot} 款式主图`} />
      ),
    },
    { title: "货号", dataIndex: "style_code_snapshot", width: 110, fixed: "left" },
    {
      // 品名 = 商品简称，没填回落建单快照（7a-8，后端 display_name.py 一处定规则）；
      // 下面一行小字颜色尺码（items，套装每个成员一行）；没有明细的旧单显示录入信息里的原文 + 「旧」
      title: "品名",
      dataIndex: "display_short_name",
      width: 160,
      render: (v: string | null, row: Promotion) => {
        const spec = itemSpecLines(row);
        return (
          <div style={{ minWidth: 0 }}>
            <div style={ONE_LINE}>
              <DisplayNameCell name={v ?? row.style_short_name_snapshot} fullTitle={row.goods_title} />
            </div>
            {spec?.lines.map((line, i) => (
              <div
                key={i}
                title={line}
                style={{ ...ONE_LINE, color: token.colorTextSecondary, fontSize: token.fontSizeSM }}
              >
                {line}
                {spec.legacy && i === 0 && (
                  <Tag style={{ marginInlineStart: 4, fontSize: token.fontSizeSM }}>旧</Tag>
                )}
              </div>
            ))}
          </div>
        );
      },
    },
    {
      // 只显示商品名（简称，没填回落全称）+ 套装标记，不显示商品编码（业务方 10-06）。
      // 编码仍能在上面的搜索框里搜到
      title: "归属商品",
      dataIndex: "goods_short_name",
      width: 150,
      render: (_: string | null, row: Promotion) => {
        const name = goodsDisplayName(row.goods_title, row.goods_short_name);
        return name ? (
          <Space size={4} wrap>
            <DisplayNameCell name={name} fullTitle={row.goods_title} />
            {row.goods_is_suit && <Tag color="purple">套装</Tag>}
          </Space>
        ) : (
          "—"
        );
      },
    },
    {
      title: "合作模式",
      dataIndex: "cooperation_mode",
      width: 100,
      render: (v: string | null) =>
        v ? (
          <Tooltip title={COOPERATION_MODE_HINT[v]}>
            <Tag color={cooperationModeColor[v]}>{v}</Tag>
          </Tooltip>
        ) : (
          <Tooltip title="历史导入数据没有这个信息。编辑时可以补一次，补完就锁定。">
            <Tag>未填</Tag>
          </Tooltip>
        ),
    },
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
      // 发货 3 态（流程线 3.3）；历史单（null）没进发货流程显示「—」。
      // 待打单悬停看推送人与时间，已发货悬停看快递公司 + 单号 + 发货时间
      title: "发货",
      dataIndex: "ship_status",
      width: 100,
      render: (v: string | null, row: Promotion) => {
        if (!v) return <span style={{ color: token.colorTextSecondary }}>—</span>;
        const style = shipStatusStyle(v);
        const detail = shipDetailLines(row);
        const tag = <Tag color={style.color}>{style.label}</Tag>;
        return detail.length ? (
          <Tooltip
            title={
              <div>
                {detail.map((line) => (
                  <div key={line}>{line}</div>
                ))}
              </div>
            }
          >
            {tag}
          </Tooltip>
        ) : (
          tag
        );
      },
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
      // 放得下「上轮驳回：流量差补发」这个 Tag（7a-4）
      width: 150,
      render: (v: string, row) => (
        <Space size={4} wrap>
          <Tag color={settlementColor[v]}>{v}</Tag>
          {row.review_reason_category &&
            (v === "已驳回" ? (
              <Tooltip title={row.review_reason ?? undefined}>
                <Tag color="volcano">{row.review_reason_category}</Tag>
              </Tooltip>
            ) : (
              // 重提后（或再审通过后）上一轮驳回原因仍保留，标成「上轮」免得误读成现在被驳回
              <Tooltip
                title={
                  <div>
                    {row.review_reason && <div>驳回说明：{row.review_reason}</div>}
                    {row.resubmit_note && <div>重提说明：{row.resubmit_note}</div>}
                    {row.resubmitted_at && (
                      <div>
                        重提时间：{dayjs(row.resubmitted_at).format("YYYY-MM-DD HH:mm")}
                      </div>
                    )}
                  </div>
                }
              >
                <Tag>上轮驳回：{row.review_reason_category}</Tag>
              </Tooltip>
            ))}
        </Space>
      ),
    },
    {
      title: "召回",
      dataIndex: "recall_status",
      width: 100,
      render: (v: string) =>
        v === "未召回" ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Tag color={recallColor[v]}>{v}</Tag>
        ),
    },
    {
      title: "寄回单号",
      dataIndex: "return_waybill",
      width: 140,
      render: (v: string | null, row) => {
        if (v) return <Typography.Text copyable>{v}</Typography.Text>;
        if (row.cooperation_mode === "寄拍") {
          return (
            <Tooltip title="寄拍模式没有寄回单号无法通过审核">
              <Tag color="orange">待填</Tag>
            </Tooltip>
          );
        }
        return "—";
      },
    },
    {
      // 复盘状态（PRD 改动 4）。与结款状态正交，所以单列一列而不是挤进结款那列
      title: "复盘",
      dataIndex: "retro_status",
      width: 110,
      render: (v: RetroStatus, row) =>
        v === "未开始" ? (
          row.settlement_status === "已付款" ? (
            <Tooltip title="已结款，发布满 7 天后可录数据">
              <Tag color="blue">待录数据</Tag>
            </Tooltip>
          ) : (
            "—"
          )
        ) : (
          <Tag color={retroColor[v]}>{v}</Tag>
        ),
    },
    ...SOURCE_LIST_COLUMNS.map((f) => ({
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
        const flowItems = shipMenuItems(record);
        const legacyItems = [
          {
            key: "extra",
            label: "录入信息",
            onClick: () => setExtraTarget(record),
          },
          {
            key: "goods",
            label: "改归属商品",
            onClick: () => setGoodsTarget(record),
          },
          {
            key: "publish",
            label: "标记发布",
            disabled: record.publish_status !== "未发布",
            onClick: () => {
              setPublishTarget(record);
              setPublishOpen(true);
            },
          },
          {
            key: "cancel",
            label: "取消",
            disabled: record.publish_status !== "未发布",
            onClick: () => setCancelTarget(record),
          },
          // 寄拍的审核门槛是寄回单号，所以单独给一个录入口，不用翻到「录入信息」里找
          ...(record.cooperation_mode === "寄拍"
            ? [
                {
                  key: "waybill",
                  label: record.return_waybill ? "改寄回单号" : "填寄回单号",
                  onClick: () => setWaybillTarget(record),
                },
              ]
            : []),
          {
            // 催发任务在这里发起最顺手：PR 本来就在这页看哪单还没发出来。
            // 与催发任务页共用 UrgeModal，可以直接附聊天截图（7a-3）
            key: "urge",
            label: "催发",
            disabled: !["未发布", "异常"].includes(record.publish_status),
            onClick: () => setUrgeTarget(record),
          },
          {
            key: "recall",
            label: "召回",
            // 召回要求已发布或已取消（后端 BR-U04-24），召回成功是终态
            disabled:
              !["已发布", "已取消"].includes(record.publish_status) ||
              record.recall_status === "召回成功",
            onClick: () => setRecallTarget(record),
          },
          {
            key: "approve",
            label: "审核通过",
            // 只有待核查的单据能审。以前没有这个门槛，点了必然 422
            disabled: record.settlement_status !== "待核查",
            onClick: () => approveMutation.mutate(record.id),
          },
          {
            key: "reject",
            label: "审核驳回",
            danger: true,
            disabled: record.settlement_status !== "待核查",
            onClick: () => setRejectTarget(record),
          },
          {
            // 驳回后 PR 改完重新交给主管（已驳回 → 待核查）。后端还要求「已发布」
            // （否则进了待核查也批不了），这里对齐，免得点了得到 409
            key: "resubmit",
            label: "重新提交",
            disabled:
              record.settlement_status !== "已驳回" ||
              record.publish_status !== "已发布",
            onClick: () => setResubmitTarget(record),
          },
          // 复盘三步（PRD 改动 4）。每一步的 disabled 条件都对着后端的状态机门槛，
          // 点了不会白跑一次 422
          {
            key: "metrics",
            label: "录 7 天数据",
            disabled:
              record.settlement_status !== "已付款" ||
              record.retro_status !== "未开始",
            onClick: () => setMetricsTarget(record),
          },
          {
            key: "retro",
            label: "写复盘",
            disabled: record.retro_status !== "待复盘",
            onClick: () => setRetroTarget(record),
          },
          {
            key: "retro-confirm",
            label: "确认复盘",
            disabled: record.retro_status !== "待确认",
            onClick: () => setRetroConfirmTarget(record),
          },
          {
            // 金额改动记录。后端按字段级权限门控 —— 看不到金额的角色会拿到 403，
            // 所以这里不按角色隐藏菜单项，让后端给出一致的拒绝
            key: "amount-log",
            label: "金额改动记录",
            onClick: () => setAmountLogTarget(record),
          },
        ];
        const items: MenuProps["items"] = flowItems.length
          ? [...flowItems, { type: "divider" as const }, ...legacyItems]
          : legacyItems;
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
            templateColumns={SOURCE_FIELD_NAMES}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setOpen(true)}
          >
            新建推广
          </Button>
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索内部编码 / 货号 / 商品简称 / 商品编码"
          allowClear
          style={{ width: 300 }}
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
        <Select<ShipStatusFilter>
          placeholder="发货"
          aria-label="发货"
          allowClear
          style={{ width: 140 }}
          options={SHIP_FILTER_OPTIONS}
          onChange={(v) =>
            setFilters((f) => ({ ...f, ship_status: v ?? undefined, page: 1 }))
          }
        />
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 2530 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 10,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <CreatePromotionModal open={open} onClose={() => setOpen(false)} />

      <ChangeGoodsModal target={goodsTarget} onClose={() => setGoodsTarget(null)} />

      <CancelPromotionModal target={cancelTarget} onClose={() => setCancelTarget(null)} />

      <RejectModal target={rejectTarget} onClose={() => setRejectTarget(null)} />

      <ResubmitModal target={resubmitTarget} onClose={() => setResubmitTarget(null)} />

      <ReturnWaybillModal target={waybillTarget} onClose={() => setWaybillTarget(null)} />

      <UrgeModal
        open={!!urgeTarget}
        promotionId={urgeTarget?.id ?? null}
        title={urgeTarget ? `催发 · ${urgeTarget.internal_code}` : "催发"}
        onClose={() => setUrgeTarget(null)}
      />

      <MetricsModal target={metricsTarget} onClose={() => setMetricsTarget(null)} />

      <RetroModal target={retroTarget} onClose={() => setRetroTarget(null)} />

      <RetroConfirmModal
        target={retroConfirmTarget}
        onClose={() => setRetroConfirmTarget(null)}
      />

      <RecallModal target={recallTarget} onClose={() => setRecallTarget(null)} />

      <Drawer
        title={
          amountLogTarget
            ? `金额改动记录 · ${amountLogTarget.internal_code}`
            : "金额改动记录"
        }
        width={560}
        open={!!amountLogTarget}
        onClose={() => setAmountLogTarget(null)}
        destroyOnHidden
      >
        <AmountLogPanel promotionId={amountLogTarget?.id ?? null} />
      </Drawer>

      <PublishModal
        open={publishOpen}
        target={publishTarget}
        onCancel={() => setPublishOpen(false)}
        onDone={() => {
          setPublishOpen(false);
          setPublishTarget(null);
        }}
      />

      <SourceExtraModal
        target={extraTarget}
        onClose={() => setExtraTarget(null)}
        canManagePaymentQr={canManagePaymentQr}
      />

      <ShipPushModal target={shipPushTarget} onClose={() => setShipPushTarget(null)} />

      <ShipWithdrawModal
        target={shipWithdrawTarget}
        onClose={() => setShipWithdrawTarget(null)}
      />

      <ReceiverModal target={receiverTarget} onClose={() => setReceiverTarget(null)} />

      <ItemsModal target={itemsTarget} onClose={() => setItemsTarget(null)} />
    </Card>
  );
}
