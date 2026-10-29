import { useState } from "react";
import {
  Alert,
  Button,
  Card,
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
import { useSearchParams } from "react-router-dom";
import { getPromotion, promotionDetailKey, listPromotions, reviewPromotion, shipInclude } from "@/features/promotion/api";
import { useReportFilters } from "@/features/report/useReportFilters";
import { PROMOTION_FILTER_DEFAULTS, PROMOTION_FILTER_SCHEMA, promotionDimensionParams, promotionIdFromUrl, type PromotionDimensions } from "@/features/promotion/promotionFilters";
import { PromotionDimensionFilters } from "@/features/promotion/components/PromotionDimensionFilters";
import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { buildMenuItems } from "@/features/flow/flowMenu";
import { LineDrawer } from "@/features/flow/LineDrawer";
import { LINE_ROW_CLASS, LINE_ROW_IGNORE_CLASS, lineRowProps } from "@/features/flow/lineRow";
import type {
  Promotion,
  PromotionListFilters,
  RetroStatus,
  ShipStatusFilter,
} from "@/features/promotion/types";
import { itemSpecLines, promotionImages, shipDetailLines } from "@/features/promotion/shipDisplay";
import { promotionSettlementStyle, shipStatusStyle } from "@/features/flow/stageStyle";
import {
  approveConfirmText,
  expandReviewItem,
  metricsBadge,
  reviewOutcomeText,
} from "@/features/promotion/settlementFlow";
import { goodsDisplayName } from "@/features/product/api";
import {
  COOPERATION_MODE_HINT,
  PROMOTION_IMPORT_COLUMNS,
  SOURCE_LIST_COLUMNS,
  recallColor,
} from "@/features/promotion/listConstants";
import { AmountLogPanel } from "@/features/promotion/components/AmountLogPanel";
import { CancelPromotionModal } from "@/features/promotion/components/CancelPromotionModal";
import { ChangeGoodsModal } from "@/features/promotion/components/ChangeGoodsModal";
import { CreatePromotionModal } from "@/features/promotion/components/CreatePromotionModal";
import { PromotionDetailPanel } from "@/features/promotion/components/PromotionDetailPanel";
import { UrgeRecordsPanel } from "@/features/promotion/components/UrgeRecordsPanel";
import { promotionUrgeRecordsKey } from "@/features/urge/api";
import { MetricsModal } from "@/features/promotion/components/MetricsModal";
import { PublishModal } from "@/features/promotion/components/PublishModal";
import { RecallModal } from "@/features/promotion/components/RecallModal";
import { RecallWaybillModal } from "@/features/promotion/components/RecallWaybillModal";
import { RecallResultModal } from "@/features/promotion/components/RecallResultModal";
import { FreightModal, type FreightModalMode } from "@/features/promotion/components/FreightModal";
import { RejectModal } from "@/features/promotion/components/RejectModal";
import { ResubmitModal } from "@/features/promotion/components/ResubmitModal";
import { RetroModal } from "@/features/promotion/components/RetroModal";
import { ReturnWaybillModal } from "@/features/promotion/components/ReturnWaybillModal";
import { SourceExtraModal } from "@/features/promotion/components/SourceExtraModal";
import { PaymentQrModal } from "@/features/promotion/components/PaymentQrModal";
import { SettlementChatModal } from "@/features/promotion/components/SettlementChatModal";
import {
  SettlementResubmitModal,
  type SettlementResubmitTarget,
} from "@/features/promotion/components/SettlementResubmitModal";
import { ItemsModal } from "@/features/promotion/components/ItemsModal";
import { ReceiverModal } from "@/features/promotion/components/ReceiverModal";
import {
  ShipPushModal,
  type ShipPushTarget,
} from "@/features/promotion/components/ShipPushModal";
import { ShipWithdrawModal } from "@/features/promotion/components/ShipWithdrawModal";
import { useFlowFeedback } from "@/features/promotion/components/useFlowFeedback";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { GoodsImages } from "@/components/GoodsImages/GoodsImages";
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

/** 复盘状态配色（PRD 改动 4）。「未开始」不上色，由渲染逻辑决定显示什么。 */
const retroColor: Record<string, string> = {
  待复盘: "gold",
  已完成: "green",
};

/** 主线抽屉的页签（流程线 6.6、8.4）。 */
type LineTab = "detail" | "amount" | "urge";

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
  // 谈款定稿后「去推广管理」带 ?keyword=内部编码 过来（流程线 8.3）
  const [searchParams, setSearchParams] = useSearchParams();
  const initialKeyword = searchParams.get("keyword")?.trim() || undefined;
  const [filters, setFilters] = useState<PromotionListFilters>({
    page: 1,
    page_size: 10,
    keyword: initialKeyword,
  });
  const dimensions = useReportFilters("promotion_list", PROMOTION_FILTER_SCHEMA, PROMOTION_FILTER_DEFAULTS);
  const queryFilters = { ...filters, ...promotionDimensionParams(dimensions.filters) };
  const changeDimensions = (patch: Partial<PromotionDimensions>) => {
    dimensions.setFilters(patch);
    setFilters((f) => ({ ...f, page: 1 }));
  };
  const rawDetailId = searchParams.get("id");
  const detailId = promotionIdFromUrl(rawDetailId);
  const landingQuery = useQuery({
    queryKey: promotionDetailKey(detailId ?? ""),
    queryFn: () => getPromotion(detailId!),
    enabled: Boolean(detailId),
    retry: false,
  });
  const removeDetailId = () => setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    next.delete("id");
    return next;
  }, { replace: true });
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
  // 点一行（或「留言」「金额改动记录」菜单）开主线抽屉；tab = 打开时先显示的页签
  const [manualLineTarget, setLineTarget] = useState<{ row: Promotion; tab?: LineTab } | null>(null);
  // URL目标直接由当前ID的查询推导；不以异步effect写回旧目标，关闭后迟到响应也不能重开。
  const lineTarget = rawDetailId !== null
    ? (detailId && landingQuery.data?.id === detailId ? { row: landingQuery.data, tab: undefined } : null)
    : manualLineTarget;
  const [recallTarget, setRecallTarget] = useState<Promotion | null>(null);
  const [recallWaybillTarget, setRecallWaybillTarget] = useState<Promotion | null>(null);
  const [recallResultTarget, setRecallResultTarget] = useState<Promotion | null>(null);
  const [freightTarget, setFreightTarget] = useState<{ row: Promotion; mode: FreightModalMode } | null>(null);
  // 发货（流程线 8.4）：按行 ui 出菜单项，弹窗在 features/promotion/components/
  const [shipPushTarget, setShipPushTarget] = useState<ShipPushTarget | null>(null);
  const [shipWithdrawTarget, setShipWithdrawTarget] = useState<Promotion | null>(null);
  const [receiverTarget, setReceiverTarget] = useState<Promotion | null>(null);
  const [itemsTarget, setItemsTarget] = useState<Promotion | null>(null);
  // 结款（流程线 PR-6，8.4）：收款码 / 结款重提 / 传结款截图，按行 ui 出菜单项
  const [paymentQrTarget, setPaymentQrTarget] = useState<Promotion | null>(null);
  const [settlementResubmitTarget, setSettlementResubmitTarget] =
    useState<SettlementResubmitTarget | null>(null);
  const [settlementChatTarget, setSettlementChatTarget] = useState<Promotion | null>(null);
  const flowFeedback = useFlowFeedback();
  const openLine = (row: Promotion, tab?: LineTab) => {
    if (row.line_id) {
      if (rawDetailId !== null) removeDetailId();
      setLineTarget({ row, tab });
    }
  };
  /** 抽屉页签（6.6）：单据详情 / 金额改动（按页级 ui.actions.amount_log 出）/ 催发记录。 */
  const lineTabs = (row: Promotion) => [
    {
      key: "detail",
      label: "单据详情",
      children: (
        <PromotionDetailPanel
          promotionId={row.id}
          onEditReceiver={setReceiverTarget}
          onEditItems={setItemsTarget}
          onEditRetro={setRetroTarget}
          onRecallStart={setRecallTarget}
          onRecallWaybill={setRecallWaybillTarget}
          onRecallResult={setRecallResultTarget}
          onPaymentQr={setPaymentQrTarget}
          onFreight={(row, mode) => setFreightTarget({ row, mode })}
        />
      ),
    },
    ...(canAmountLog
      ? [{ key: "amount", label: "金额改动", children: <AmountLogPanel promotionId={row.id} /> }]
      : []),
    { key: "urge", label: "催发记录", children: <UrgeRecordsPanel promotionId={row.id} /> },
  ];

  const { data, isLoading } = useQuery({
    queryKey: ["promotions", queryFilters],
    queryFn: () => listPromotions(queryFilters),
    enabled: dimensions.ready,
  });
  // 「金额改动」页签与菜单只看页级标记（与金额改动接口同一判定），不看行上的报价值
  const canAmountLog = Boolean(data?.ui?.actions?.amount_log);
  // 「新建推广」「导入站外推广」只看页级 ui（流程线 PR-5：持 promotion_create:write 才有，PR 看不到）
  const canCreate = data?.ui?.actions?.create?.state === "enabled";
  const canImport = data?.ui?.actions?.import?.state === "enabled";

  /**
   * 审核通过（R2 / R3）。成功提示按接口的 review_outcome 出，不看 settlement_status（置换单直接已结款）；
   * 缺收款码 / 寄回单号（422 FLOW_GATE_MISSING）时把缺项写进提示。
   */
  const approveMutation = useMutation({
    mutationFn: (id: string) => reviewPromotion(id, { action: "approve" }),
    onSuccess: (saved) => {
      message.success(reviewOutcomeText(saved.review_outcome));
      flowFeedback.refresh();
    },
    onError: (err) => {
      const missing = flowFeedback.handleError(err);
      if (missing) message.error(`缺：${missing.map((m) => m.label).join("、")}`);
    },
  });

  /** 审核通过前的确认框：写清应付与去向。 */
  function confirmApprove(record: Promotion) {
    Modal.confirm({
      title: `审核通过 · ${record.internal_code}`,
      content: approveConfirmText(record.quote_amount, record.existing_settlement_status),
      okText: "审核通过",
      cancelText: "取消",
      onOk: () => approveMutation.mutateAsync(record.id).catch(() => undefined),
    });
  }

  /** 纳入发货：历史单 → 待发货（之后才出「确认推送仓库」）。 */
  const shipIncludeMutation = useMutation({
    mutationFn: (id: string) => shipInclude(id),
    onSuccess: () => {
      message.success("已纳入发货，进入待推送仓库");
      flowFeedback.refresh();
    },
    onError: (err) => flowFeedback.handleError(err),
  });

  /**
   * 按 row.ui 出的菜单项（发货、催发、审核、结款、录 7 天数据、留言、改归属商品 / 收件 / 颜色尺码 / 收款码）：
   * 顺序与禁用原因由 buildMenuItems 定；没有的项不出现。审核一个动作拆成通过 / 驳回两项（expandReviewItem）。
   */
  function flowMenuItems(record: Promotion): NonNullable<MenuProps["items"]> {
    const built = buildMenuItems("promotion", record.ui, {
      actions: {
        recall_start: { label: "发起召回", onClick: () => setRecallTarget(record) },
        recall_waybill: { label: "上传召回寄回截图", onClick: () => setRecallWaybillTarget(record) },
        recall_result: { label: "登记召回结果", onClick: () => setRecallResultTarget(record) },
        freight_submit: { label: "提交寄回运费", onClick: () => setFreightTarget({ row: record, mode: "submit" }) },
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
        // 催发任务在这里发起最顺手：PR 本来就在这页看哪单还没发出来。
        // 与催发任务页共用 UrgeModal，可以直接附聊天截图（7a-3）
        urge: { label: "催发", onClick: () => setUrgeTarget(record) },
        // 审核（★¹⁵）：缺收款码 / 寄回单号时 expandReviewItem 把「审核通过」禁用并写缺项，驳回照常
        review: { label: "审核通过", onClick: () => confirmApprove(record) },
        settlement_resubmit: {
          label: "结款重提",
          onClick: (action) => setSettlementResubmitTarget({ row: record, action }),
        },
        settlement_chat: { label: "传结款截图", onClick: () => setSettlementChatTarget(record) },
        // 门槛（已结款、满 7 天）以接口为准：灰显时 label 带「YYYY-MM-DD 起可录」「已结款后可录」
        metrics: { label: "录 7 天数据", onClick: () => setMetricsTarget(record) },
        // 留言在抽屉底部（6.6）：菜单项只负责打开抽屉
        ...(record.ui?.actions?.retro_write?.state === "enabled" ? {
          retro_write: { label: "写复盘", onClick: () => setRetroTarget(record) },
        } : {}),
        retro_revise: { label: "修改复盘", onClick: () => setRetroTarget(record) },
        comment: { label: "留言", onClick: () => openLine(record) },
      },
      edits: {
        // PR-5 起归属商品只有主管、管理员能改（11-53），PR 的 edits 里没有 goods
        goods: { label: "改归属商品", onClick: () => setGoodsTarget(record) },
        receiver: { label: "改收件信息", onClick: () => setReceiverTarget(record) },
        goods_items: { label: "改颜色尺码", onClick: () => setItemsTarget(record) },
        payment_qr: { label: "收款码", onClick: () => setPaymentQrTarget(record) },
      },
    });
    const items = expandReviewItem(built, record.ui?.actions?.review, () => setRejectTarget(record));
    items.push(...buildMenuItems("freight", record.freight_payment?.ui, { actions: {
      freight_review: { label: "审核寄回运费", onClick: () => setFreightTarget({ row: record, mode: "review" }) },
      freight_resubmit: { label: "重提寄回运费", onClick: () => setFreightTarget({ row: record, mode: "resubmit" }) },
      freight_pay: { label: "支付寄回运费", onClick: () => setFreightTarget({ row: record, mode: "pay" }) },
      freight_reject_payment: { label: "驳回寄回运费付款", onClick: () => setFreightTarget({ row: record, mode: "reject" }) },
    } }));
    // 换不了收款码（锁定 / 只读）但看得到、也传过：给一个只看的入口，弹窗里写为什么锁
    if (!record.ui?.edits?.includes("payment_qr") && record.payment_qr_attachment_id) {
      items.push({
        key: "payment_qr_view",
        label: "查看收款码",
        disabled: false,
        onClick: () => setPaymentQrTarget(record),
      });
    }
    // FlowMenuItem 是 interface（没有 antd MenuItemType 的 data-* 索引签名），逐项转成字面量
    return items.map(({ key, label, disabled, danger, onClick }) => ({
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
      // 7c-15：成员款式主图并排（套装每个成员 1 张，缺图不占位，都没有显示「暂无主图」），64px；宽度放得下 2 张
      title: "主图",
      key: "main_image",
      width: 148,
      fixed: "left",
      // 点图看大图，不开抽屉
      className: LINE_ROW_IGNORE_CLASS,
      render: (_, row) => (
        <GoodsImages
          images={promotionImages(row)}
          displayName={row.display_short_name ?? row.style_short_name_snapshot}
          size={64}
        />
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
    { title: "合作平台", dataIndex: "platform", width: 150, render: (value: string, row: Promotion) => <Space size={2} wrap><span>{value}</span>{row.extra_platforms?.map((p) => <Tag key={p}>＋{p}</Tag>)}</Space> },
    { title: "内容分类", dataIndex: "content_category", width: 100, render: (v: string | null) => v || "—" },
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
      // 文案按 3.4「界面显示」列（已付款 = 待传结款截图、已结款 = 已结款 等）
      render: (v: string, row) => (
        <Space size={4} wrap>
          <Tag color={promotionSettlementStyle(v).color}>{promotionSettlementStyle(v).label}</Tag>
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
      width: 150,
      render: (v: string, row: Promotion) => <Space direction="vertical" size={4}>
        {v === "未召回" ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Tag color={recallColor[v]}>{v}</Tag>
        )}
        {row.badges?.includes("freight_open") && row.freight_payment && <Tag color="orange">运费单：{row.freight_payment.status}</Tag>}
      </Space>,
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
      // 复盘状态（PRD 改动 4）。与结款状态正交，所以单列一列而不是挤进结款那列。
      // 7 天数据的提示读接口的 badges（满 7 天可录 / 满 7 天 · 待结款），不按结款状态推
      title: "复盘",
      dataIndex: "retro_status",
      width: 130,
      render: (v: RetroStatus, row) => {
        const badge = metricsBadge(row.badges);
        if (v === "未开始" && !badge) return "—";
        return (
          <Space size={4} wrap>
            {v !== "未开始" && <Tag color={retroColor[v]}>{v}</Tag>}
            {badge && (
              <Tooltip title={badge.tip}>
                <Tag color={badge.color}>{badge.label}</Tag>
              </Tooltip>
            )}
          </Space>
        );
      },
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
      className: LINE_ROW_IGNORE_CLASS,
      render: (_, record) => {
        const flowItems = flowMenuItems(record);
        const legacyItems = [
          {
            key: "extra",
            // 收款码已移到「收款码」菜单项（PaymentQrModal），这里只剩订单号等
            label: "其他信息",
            onClick: () => setExtraTarget(record),
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
            // 驳回后 PR 改完重新交给主管（已驳回 → 待核查）。后端还要求「已发布」
            // （否则进了待核查也批不了），这里对齐，免得点了得到 409
            key: "resubmit",
            label: "重新提交",
            disabled:
              record.settlement_status !== "已驳回" ||
              record.publish_status !== "已发布",
            onClick: () => setResubmitTarget(record),
          },
          // 金额改动记录 = 主线抽屉的一个页签（8.4）：打开抽屉并切过去。
          // 页级 ui.actions.amount_log 没有（读不到金额改动接口）就没有这个页签，菜单项也不出
          ...(record.line_id && canAmountLog
            ? [
                {
                  key: "amount-log",
                  label: "金额改动记录",
                  onClick: () => openLine(record, "amount"),
                },
              ]
            : []),
        ];
        // 已完结H列只有真正*能编辑合作信息；沿用后端矩阵能力守住尚未纳入PR-10的旧写菜单。
        const completedReadonly = !!record.completed_at && !record.ui?.edits?.includes("cooperation");
        const guardedLegacy = legacyItems.map((item) => completedReadonly && item.key !== "amount-log"
          ? { ...item, label: `${item.label}（已完结，只读）`, disabled: true, onClick: undefined }
          : item);
        const items: MenuProps["items"] = flowItems.length
          ? [...flowItems, { type: "divider" as const }, ...guardedLegacy]
          : guardedLegacy;
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
        canCreate || canImport ? (
          <Space>
            {canImport && (
              <ImportUploadButton
                source="manual_promotion"
                label="导入站外推广"
                invalidateKeys={[["promotions"]]}
                templateColumns={PROMOTION_IMPORT_COLUMNS}
              />
            )}
            {canCreate && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setOpen(true)}
              >
                新建推广
              </Button>
            )}
          </Space>
        ) : undefined
      }
    >
      <PromotionDimensionFilters value={dimensions.filters} onChange={changeDimensions} />
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索内部编码 / 货号 / 商品简称 / 商品编码"
          defaultValue={initialKeyword}
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

      {rawDetailId !== null && (!detailId || landingQuery.isError) ? (
        <Alert type="warning" showIcon closable style={{ marginBottom: 16 }}
          message={!detailId ? "推广编号无效" : "无法打开推广详情，单据不存在或没有访问权限"}
          onClose={() => { setLineTarget(null); removeDetailId(); }} />
      ) : null}

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        // 点一行（操作列、主图、按钮链接以外）或 Enter 开主线抽屉（8.4）
        rowClassName={(r) => (r.line_id ? LINE_ROW_CLASS : "")}
        onRow={(r) => (r.line_id ? lineRowProps(() => openLine(r)) : {})}
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
        onUrged={() => {
          void qc.invalidateQueries({ queryKey: LINE_EVENTS_ALL });
          if (urgeTarget) void qc.invalidateQueries({ queryKey: promotionUrgeRecordsKey(urgeTarget.id) });
        }}
      />

      <MetricsModal target={metricsTarget} onClose={() => setMetricsTarget(null)} />

      <RetroModal target={retroTarget} onClose={() => setRetroTarget(null)} />



      <RecallModal target={recallTarget} onClose={() => setRecallTarget(null)} />
      <RecallWaybillModal target={recallWaybillTarget} onClose={() => setRecallWaybillTarget(null)} />
      <RecallResultModal target={recallResultTarget} onClose={() => setRecallResultTarget(null)} />
      <FreightModal target={freightTarget?.row ?? null} mode={freightTarget?.mode ?? "view"} onClose={() => setFreightTarget(null)} />

      <LineDrawer
        lineId={lineTarget?.row.line_id ?? null}
        onClose={() => { setLineTarget(null); if (rawDetailId !== null) removeDetailId(); }}
        goodsName={
          lineTarget ? lineTarget.row.display_short_name ?? lineTarget.row.style_short_name_snapshot : null
        }
        defaultTab={lineTarget?.tab}
        tabs={lineTarget ? lineTabs(lineTarget.row) : undefined}
      />

      <PublishModal
        open={publishOpen}
        target={publishTarget}
        onCancel={() => setPublishOpen(false)}
        onDone={() => {
          setPublishOpen(false);
          setPublishTarget(null);
        }}
      />

      <SourceExtraModal target={extraTarget} onClose={() => setExtraTarget(null)} />

      <PaymentQrModal target={paymentQrTarget} onClose={() => setPaymentQrTarget(null)} />

      <SettlementResubmitModal
        target={settlementResubmitTarget}
        onClose={() => setSettlementResubmitTarget(null)}
      />

      <SettlementChatModal
        target={settlementChatTarget}
        onClose={() => setSettlementChatTarget(null)}
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
