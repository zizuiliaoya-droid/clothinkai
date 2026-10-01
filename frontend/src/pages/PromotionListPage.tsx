import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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
  Tooltip,
  Typography,
  Upload,
  message,
} from "antd";
import {
  DeleteOutlined,
  DownOutlined,
  EyeOutlined,
  PlusOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  cancelPromotion,
  createPromotion,
  listPromotions,
  publishPromotion,
  recallFailurePromotion,
  recallSuccessPromotion,
  removePaymentQr,
  reviewPromotion,
  setReturnWaybill,
  startRecallPromotion,
  updatePromotion,
  uploadPaymentQrFile,
} from "@/features/promotion/api";
import type {
  Promotion,
  PromotionCreate,
  PromotionListFilters,
  RejectReasonCategory,
} from "@/features/promotion/types";
import {
  listStyles,
  listSkusByStyle,
  listGoodsForStyle,
  type GoodsOption,
} from "@/features/product/api";
import { listBloggers } from "@/features/blogger/api";
import { urgePromotion } from "@/features/urge/api";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";

const PLATFORMS = ["小红书", "抖音", "快手", "B站"];
const PUBLISH_STATUS = ["未发布", "已发布", "已取消", "异常", "已删除"];

// 站外推广人工源列（对齐 final.xlsx），从 source_extra 读取
type SourceField = {
  name: string;
  type: "text" | "number" | "select";
  options?: string[];
};
const SOURCE_FIELDS: SourceField[] = [
  { name: "颜色及规格", type: "text" },
  { name: "打单地址", type: "text" },
  { name: "发货单号", type: "text" },
  { name: "订单号", type: "text" },
  { name: "寄回单号", type: "text" },
  // 「合作方式」已提成 typed 字段 cooperation_mode，不再走 source_extra —— 它决定成本
  // 口径与审核后的流转出口，必须是后端能校验的字段。
  { name: "合作形式", type: "select", options: ["线下", "拍单"] },
  { name: "负责PR", type: "text" },
  { name: "点赞数", type: "number" },
  { name: "收藏数", type: "number" },
  { name: "评论数", type: "number" },
];
const SOURCE_FIELD_NAMES = SOURCE_FIELDS.map((f) => f.name);

/** 合作模式。单据生成后不可改，所以只在新建表单里出现。 */
const COOPERATION_MODES = ["寄拍", "送拍", "置换"] as const;

const COOPERATION_MODE_HINT: Record<string, string> = {
  寄拍: "衣服要寄回，样品成本记 0，只有寄回运费计入成本",
  送拍: "衣服送给博主，样品成本取商品成员款式的货品成本之和",
  置换: "以货换推广，没有博主服务费",
};

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

const recallColor: Record<string, string> = {
  召回中: "orange",
  召回成功: "green",
  召回失败: "red",
};

/** 驳回原因分类，三选一必填（PRD 改动 5）。 */
const REJECT_CATEGORIES: RejectReasonCategory[] = [
  "延迟发文",
  "流量差补发",
  "衣服未寄回",
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
  const user = useAuthStore((s) => s.user);
  const canManagePaymentQr = Boolean(
    user?.roles.some((r) => ["admin", "platform_admin", "pr", "pr_manager"].includes(r))
  );
  const [filters, setFilters] = useState<PromotionListFilters>({
    page: 1,
    page_size: 10,
  });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<Promotion | null>(null);
  const [publishForm] = Form.useForm();
  const [extraOpen, setExtraOpen] = useState(false);
  const [extraTarget, setExtraTarget] = useState<Promotion | null>(null);
  const [extraForm] = Form.useForm();
  const [paymentQrFile, setPaymentQrFile] = useState<File | null>(null);
  const [paymentQrUploading, setPaymentQrUploading] = useState(false);
  // 新建推广时选中的款式 → 拉它归属的商品。只有一个就自动填，多个才需要人工选。
  const [formStyleId, setFormStyleId] = useState<string | null>(null);
  // 改归属：目标推广 + 它所属款式的商品候选
  const [goodsTarget, setGoodsTarget] = useState<Promotion | null>(null);
  const [goodsForm] = Form.useForm();
  // 取消 / 驳回 / 寄回单号 / 召回：都要填东西，各自一个弹窗。
  // 以前取消是硬编码「手动取消」直接提交，驳回则压根不传原因（后端必定 422）。
  const [cancelTarget, setCancelTarget] = useState<Promotion | null>(null);
  const [cancelForm] = Form.useForm();
  const [rejectTarget, setRejectTarget] = useState<Promotion | null>(null);
  const [rejectForm] = Form.useForm();
  const [waybillTarget, setWaybillTarget] = useState<Promotion | null>(null);
  const [waybillForm] = Form.useForm();
  const [urgeTarget, setUrgeTarget] = useState<Promotion | null>(null);
  const [urgeForm] = Form.useForm();
  const [recallTarget, setRecallTarget] = useState<Promotion | null>(null);
  const [recallForm] = Form.useForm();
  // §11：颜色及规格按货号联动——当前推广所属款式的 SKU 颜色+尺码组合
  const [colorSizeOptions, setColorSizeOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const { data, isLoading } = useQuery({
    queryKey: ["promotions", filters],
    queryFn: () => listPromotions(filters),
  });
  const { data: styles } = useQuery({
    queryKey: ["styles", "options"],
    queryFn: () => listStyles({ page: 1, page_size: 100 }),
  });
  const { data: formGoods } = useQuery({
    queryKey: ["goods", "by-style", formStyleId],
    enabled: !!formStyleId,
    queryFn: () => listGoodsForStyle(formStyleId!),
  });
  const { data: targetGoods } = useQuery({
    queryKey: ["goods", "by-style", goodsTarget?.style_id],
    enabled: !!goodsTarget,
    queryFn: () => listGoodsForStyle(goodsTarget!.style_id),
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
  const goodsOptions = (formGoods ?? []).map((g: GoodsOption) => ({
    label: `${g.goods_code} ${g.goods_title}${g.is_suit ? "（套装）" : ""}`,
    value: g.goods_main_id,
  }));
  // 款式只归属一个商品时不必打扰用户，直接用它
  const goodsChoiceNeeded = (formGoods?.length ?? 0) > 1;

  // 选完款式后自动带出商品；有歧义时清空让用户显式选
  useEffect(() => {
    if (!formGoods) return;
    form.setFieldsValue({
      goods_main_id: formGoods.length === 1 ? formGoods[0].goods_main_id : undefined,
    });
  }, [formGoods, form]);

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

  const updateExtraMutation = useMutation({
    mutationFn: ({ id, source_extra }: { id: string; source_extra: Record<string, unknown> }) =>
      updatePromotion(id, { source_extra }),
    onSuccess: () => {
      message.success("信息已保存");
      setExtraOpen(false);
      setExtraTarget(null);
      extraForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const updateGoodsMutation = useMutation({
    mutationFn: ({ id, goods_main_id }: { id: string; goods_main_id: string }) =>
      updatePromotion(id, { goods_main_id }),
    onSuccess: () => {
      message.success("归属商品已更新，投产报表的推广费会跟着调整");
      setGoodsTarget(null);
      goodsForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openGoods(record: Promotion) {
    setGoodsTarget(record);
    goodsForm.resetFields();
    goodsForm.setFieldsValue({ goods_main_id: record.goods_main_id ?? undefined });
  }

  function openExtra(record: Promotion) {
    setExtraTarget(record);
    setPaymentQrFile(null);
    const se = (record.source_extra ?? {}) as Record<string, unknown>;
    extraForm.resetFields();
    extraForm.setFieldsValue(
      Object.fromEntries(SOURCE_FIELD_NAMES.map((f) => [f, se[f] ?? ""]))
    );
    setExtraOpen(true);
    // §11：按货号(款式)加载该款 SKU 的「颜色 + 尺码」组合作为下拉选项
    setColorSizeOptions([]);
    if (record.style_id) {
      void listSkusByStyle(record.style_id)
        .then((skus) => {
          const seen = new Set<string>();
          const opts: { label: string; value: string }[] = [];
          for (const s of skus) {
            const combo = `${s.color}${s.size ? " " + s.size : ""}`.trim();
            if (combo && !seen.has(combo)) {
              seen.add(combo);
              opts.push({ label: combo, value: combo });
            }
          }
          setColorSizeOptions(opts);
        })
        .catch(() => setColorSizeOptions([]));
    }
  }

  async function uploadPaymentQr() {
    if (!extraTarget || !paymentQrFile) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(paymentQrFile.type)) {
      message.error("收款码仅支持 JPG、PNG、WebP 图片");
      return;
    }
    if (paymentQrFile.size > 10 * 1024 * 1024) {
      message.error("收款码图片不能超过 10MB");
      return;
    }
    setPaymentQrUploading(true);
    try {
      const updated = await uploadPaymentQrFile(extraTarget.id, paymentQrFile);
      setExtraTarget(updated);
      setPaymentQrFile(null);
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      message.success("博主收款码已上传");
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setPaymentQrUploading(false);
    }
  }

  async function deletePaymentQr() {
    if (!extraTarget) return;
    setPaymentQrUploading(true);
    try {
      await removePaymentQr(extraTarget.id);
      setExtraTarget({
        ...extraTarget,
        payment_qr_attachment_id: null,
        payment_qr_signed_url: null,
      });
      setPaymentQrFile(null);
      void qc.invalidateQueries({ queryKey: ["promotions"] });
      message.success("收款码已移除");
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setPaymentQrUploading(false);
    }
  }

  const cancelMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      cancelPromotion(id, { cancel_reason: reason }),
    onSuccess: () => {
      message.success("已取消");
      setCancelTarget(null);
      cancelForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
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

  /** 驳回必须带原因分类 + 文字说明，后端两者都校验。 */
  const rejectMutation = useMutation({
    mutationFn: ({
      id,
      category,
      reason,
    }: {
      id: string;
      category: RejectReasonCategory;
      reason: string;
    }) =>
      reviewPromotion(id, {
        action: "reject",
        review_reason: reason,
        review_reason_category: category,
      }),
    onSuccess: () => {
      message.success("已驳回");
      setRejectTarget(null);
      rejectForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const waybillMutation = useMutation({
    mutationFn: ({ id, waybill }: { id: string; waybill: string }) =>
      setReturnWaybill(id, waybill),
    onSuccess: () => {
      message.success("寄回单号已保存，现在可以提交审核了");
      setWaybillTarget(null);
      waybillForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const urgeMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      urgePromotion(id, note),
    onSuccess: (d) => {
      message.success(`已催发，这是第 ${d.urge_count} 次`);
      setUrgeTarget(null);
      urgeForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["urge-tasks"] });
      void qc.invalidateQueries({ queryKey: ["urge-dashboard"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const recallMutation = useMutation({
    mutationFn: ({
      id,
      step,
      reason,
    }: {
      id: string;
      step: "start" | "success" | "failure";
      reason?: string;
    }) => {
      if (step === "start") {
        return startRecallPromotion(id, { recall_reason: reason ?? null });
      }
      if (step === "success") {
        return recallSuccessPromotion(id);
      }
      return recallFailurePromotion(id);
    },
    onSuccess: () => {
      message.success("召回状态已更新");
      setRecallTarget(null);
      recallForm.resetFields();
      void qc.invalidateQueries({ queryKey: ["promotions"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function handleCreate(values: Record<string, unknown>) {
    const payload: PromotionCreate = {
      style_id: values.style_id as string,
      goods_main_id: (values.goods_main_id as string) || null,
      blogger_id: values.blogger_id as string,
      cooperation_mode: values.cooperation_mode as string,
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
    { title: "品名", dataIndex: "style_short_name_snapshot", width: 130, render: (v) => v || "—" },
    {
      title: "归属商品",
      dataIndex: "goods_code",
      width: 150,
      render: (code: string | null, row: Promotion) =>
        code ? (
          <Space size={4}>
            <span>{code}</span>
            {row.goods_is_suit && <Tag color="purple">套装</Tag>}
          </Space>
        ) : (
          "—"
        ),
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
      width: 110,
      render: (v: string, row) => (
        <Space size={4}>
          <Tag color={settlementColor[v]}>{v}</Tag>
          {row.review_reason_category && (
            <Tooltip title={row.review_reason ?? undefined}>
              <Tag color="volcano">{row.review_reason_category}</Tag>
            </Tooltip>
          )}
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
    ...SOURCE_FIELD_NAMES.map((f) => ({
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
            key: "extra",
            label: "录入信息",
            onClick: () => openExtra(record),
          },
          {
            key: "goods",
            label: "改归属商品",
            onClick: () => openGoods(record),
          },
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
            onClick: () => {
              setCancelTarget(record);
              cancelForm.resetFields();
            },
          },
          // 寄拍的审核门槛是寄回单号，所以单独给一个录入口，不用翻到「录入信息」里找
          ...(record.cooperation_mode === "寄拍"
            ? [
                {
                  key: "waybill",
                  label: record.return_waybill ? "改寄回单号" : "填寄回单号",
                  onClick: () => {
                    setWaybillTarget(record);
                    waybillForm.setFieldsValue({
                      return_waybill: record.return_waybill ?? "",
                    });
                  },
                },
              ]
            : []),
          {
            // 催发任务在这里发起最顺手：PR 本来就在这页看哪单还没发出来。
            // 带截图的催发要到催发任务页做，这里是快捷的「再催一次」
            key: "urge",
            label: "催发",
            disabled: !["未发布", "异常"].includes(record.publish_status),
            onClick: () => {
              setUrgeTarget(record);
              urgeForm.resetFields();
            },
          },
          {
            key: "recall",
            label: "召回",
            // 召回要求已发布或已取消（后端 BR-U04-24），召回成功是终态
            disabled:
              !["已发布", "已取消"].includes(record.publish_status) ||
              record.recall_status === "召回成功",
            onClick: () => {
              setRecallTarget(record);
              recallForm.resetFields();
            },
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
          placeholder="搜索内部编码 / 货号"
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
              onChange={(v: string) => setFormStyleId(v)}
            />
          </Form.Item>
          {goodsChoiceNeeded && (
            <Form.Item
              name="goods_main_id"
              label="归属商品"
              tooltip="这个款式既单卖又进了套装，推广费要算给哪个商品由你决定"
              rules={[{ required: true, message: "请选择这次推广归属的商品" }]}
            >
              <Select placeholder="选择归属商品" options={goodsOptions} />
            </Form.Item>
          )}
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
          <Form.Item
            name="cooperation_mode"
            label="合作模式"
            rules={[{ required: true, message: "请选择合作模式" }]}
            tooltip="决定样品成本与博主服务费怎么算，以及审核通过后走哪个流程。单据建好后不能改。"
          >
            <Select
              placeholder="选择合作模式"
              options={COOPERATION_MODES.map((m) => ({
                label: `${m} · ${COOPERATION_MODE_HINT[m]}`,
                value: m,
              }))}
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
          <Form.Item
            name="quote_amount"
            label="报价金额"
            tooltip="置换模式没有博主服务费，填了也会被置 0"
          >
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
        title={
          goodsTarget
            ? `改归属商品 · ${goodsTarget.style_code_snapshot} ${goodsTarget.style_short_name_snapshot}`
            : "改归属商品"
        }
        open={!!goodsTarget}
        onCancel={() => setGoodsTarget(null)}
        onOk={() => goodsForm.submit()}
        confirmLoading={updateGoodsMutation.isPending}
        destroyOnHidden
        width={520}
      >
        <Form
          form={goodsForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(values: { goods_main_id: string }) => {
            if (!goodsTarget) return;
            updateGoodsMutation.mutate({
              id: goodsTarget.id,
              goods_main_id: values.goods_main_id,
            });
          }}
        >
          <Form.Item
            name="goods_main_id"
            label="归属商品"
            tooltip="决定这笔推广费算给哪个商品的投产比。只能选包含该款式的商品。"
            rules={[{ required: true, message: "请选择归属商品" }]}
          >
            <Select
              placeholder="选择归属商品"
              options={(targetGoods ?? []).map((g: GoodsOption) => ({
                label: `${g.goods_code} ${g.goods_title}${g.is_suit ? "（套装）" : ""}`,
                value: g.goods_main_id,
              }))}
            />
          </Form.Item>
          {(targetGoods?.length ?? 0) <= 1 && (
            <Typography.Text type="secondary">
              该款式只归属一个商品，无需调整。
            </Typography.Text>
          )}
        </Form>
      </Modal>

      <Modal
        title={cancelTarget ? `取消 · ${cancelTarget.internal_code}` : "取消推广"}
        open={!!cancelTarget}
        onCancel={() => setCancelTarget(null)}
        onOk={() => cancelForm.submit()}
        confirmLoading={cancelMutation.isPending}
        okButtonProps={{ danger: true }}
        okText="确认取消"
        destroyOnHidden
      >
        <Form
          form={cancelForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v: { cancel_reason: string }) => {
            if (!cancelTarget) return;
            cancelMutation.mutate({
              id: cancelTarget.id,
              reason: v.cancel_reason,
            });
          }}
        >
          <Form.Item
            name="cancel_reason"
            label="取消原因"
            rules={[{ required: true, message: "请填写取消原因" }]}
            tooltip="取消是终态，不能撤回。原因会写入操作记录。"
          >
            <Input.TextArea rows={3} placeholder="如：博主档期冲突，不再合作" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={rejectTarget ? `驳回 · ${rejectTarget.internal_code}` : "驳回"}
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
          onFinish={(v: {
            review_reason_category: RejectReasonCategory;
            review_reason: string;
          }) => {
            if (!rejectTarget) return;
            rejectMutation.mutate({
              id: rejectTarget.id,
              category: v.review_reason_category,
              reason: v.review_reason,
            });
          }}
        >
          <Form.Item
            name="review_reason_category"
            label="驳回原因分类"
            rules={[{ required: true, message: "请选择驳回原因分类" }]}
            tooltip="分类用于统计哪类问题最多，也决定后续动作（衣服未寄回要催寄回，流量差补发要重新排期）。"
          >
            <Select
              placeholder="三选一"
              options={REJECT_CATEGORIES.map((c) => ({ label: c, value: c }))}
            />
          </Form.Item>
          <Form.Item
            name="review_reason"
            label="说明"
            rules={[{ required: true, message: "请填写驳回说明" }]}
          >
            <Input.TextArea rows={3} placeholder="具体说明，PR 会看到这段文字" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={
          waybillTarget
            ? `博主寄回衣服单号 · ${waybillTarget.internal_code}`
            : "博主寄回衣服单号"
        }
        open={!!waybillTarget}
        onCancel={() => setWaybillTarget(null)}
        onOk={() => waybillForm.submit()}
        confirmLoading={waybillMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={waybillForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v: { return_waybill: string }) => {
            if (!waybillTarget) return;
            waybillMutation.mutate({
              id: waybillTarget.id,
              waybill: v.return_waybill,
            });
          }}
        >
          <Typography.Paragraph type="secondary">
            寄拍模式下，没有这个单号审核通不过，财务也看不到单据。这是博主把衣服寄回来的
            快递单号，不是寄给博主的那个。
          </Typography.Paragraph>
          <Form.Item
            name="return_waybill"
            label="寄回快递单号"
            rules={[{ required: true, message: "请填写寄回单号" }]}
          >
            <Input placeholder="如 SF1234567890" allowClear />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={urgeTarget ? `催发 · ${urgeTarget.internal_code}` : "催发"}
        open={!!urgeTarget}
        onCancel={() => setUrgeTarget(null)}
        onOk={() => urgeForm.submit()}
        confirmLoading={urgeMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={urgeForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v: { note?: string }) => {
            if (!urgeTarget) return;
            urgeMutation.mutate({ id: urgeTarget.id, note: v.note });
          }}
        >
          <Typography.Paragraph type="secondary">
            记一次催发。第一次催会自动建催发任务，之后累加次数。要附聊天截图请到
            <Link to="/urge-tasks">催发任务</Link> 页操作。
          </Typography.Paragraph>
          <Form.Item name="note" label="备注">
            <Input.TextArea
              rows={3}
              placeholder="怎么催的、博主怎么回的（可选，会写进催发时间线）"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={recallTarget ? `召回 · ${recallTarget.internal_code}` : "召回"}
        open={!!recallTarget}
        onCancel={() => setRecallTarget(null)}
        footer={null}
        destroyOnHidden
      >
        {recallTarget && (
          <div style={{ marginTop: 16 }}>
            <Typography.Paragraph type="secondary">
              当前召回状态：<Tag color={recallColor[recallTarget.recall_status]}>
                {recallTarget.recall_status}
              </Tag>
              衣服损坏或要寄回都走召回流程，与合作模式无关。
            </Typography.Paragraph>
            {["未召回", "召回失败"].includes(recallTarget.recall_status) && (
              <Form
                form={recallForm}
                layout="vertical"
                onFinish={(v: { recall_reason?: string }) =>
                  recallMutation.mutate({
                    id: recallTarget.id,
                    step: "start",
                    reason: v.recall_reason,
                  })
                }
              >
                <Form.Item name="recall_reason" label="召回原因">
                  <Input.TextArea rows={2} placeholder="如：衣服有污损，要求寄回（可选）" />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={recallMutation.isPending}
                >
                  发起召回
                </Button>
              </Form>
            )}
            {recallTarget.recall_status === "召回中" && (
              <Space>
                <Button
                  type="primary"
                  loading={recallMutation.isPending}
                  onClick={() =>
                    recallMutation.mutate({
                      id: recallTarget.id,
                      step: "success",
                    })
                  }
                >
                  召回成功
                </Button>
                <Button
                  danger
                  loading={recallMutation.isPending}
                  onClick={() =>
                    recallMutation.mutate({
                      id: recallTarget.id,
                      step: "failure",
                    })
                  }
                >
                  召回失败
                </Button>
                <Typography.Text type="secondary">
                  失败后还能重新发起
                </Typography.Text>
              </Space>
            )}
            {recallTarget.recall_status === "召回成功" && (
              <Typography.Text type="secondary">
                召回已完成，这是终态。寄回运费可以在「录入信息」里补。
              </Typography.Text>
            )}
          </div>
        )}
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

      <Modal
        title="录入推广信息（地址/订单号等）"
        open={extraOpen}
        onCancel={() => {
          setExtraOpen(false);
          setExtraTarget(null);
          setPaymentQrFile(null);
          extraForm.resetFields();
        }}
        onOk={() => extraForm.submit()}
        confirmLoading={updateExtraMutation.isPending}
        destroyOnHidden
        width={560}
      >
        <Form
          form={extraForm}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(values: Record<string, unknown>) => {
            if (!extraTarget) return;
            // 仅提交非空字段，空值不覆盖
            const source_extra: Record<string, unknown> = {
              ...((extraTarget.source_extra ?? {}) as Record<string, unknown>),
            };
            for (const f of SOURCE_FIELD_NAMES) {
              const v = values[f];
              if (v === undefined || v === null || String(v).trim() === "") {
                delete source_extra[f];
              } else {
                source_extra[f] = String(v).trim();
              }
            }
            updateExtraMutation.mutate({ id: extraTarget.id, source_extra });
          }}
        >
          {canManagePaymentQr && (
            <>
              <Form.Item label="结款信息（博主收款码）" style={{ marginBottom: 12 }}>
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
                  {extraTarget?.payment_qr_signed_url ? (
                    <Typography.Link
                      href={extraTarget.payment_qr_signed_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <EyeOutlined /> 查看当前收款码
                    </Typography.Link>
                  ) : (
                    <Typography.Text type="secondary">尚未上传收款码</Typography.Text>
                  )}
                  <Upload
                    accept="image/jpeg,image/png,image/webp"
                    maxCount={1}
                    beforeUpload={(file) => {
                      setPaymentQrFile(file);
                      return false;
                    }}
                    onRemove={() => setPaymentQrFile(null)}
                    fileList={
                      paymentQrFile
                        ? [{ uid: "payment-qr", name: paymentQrFile.name }]
                        : []
                    }
                  >
                    <Button icon={<UploadOutlined />} disabled={paymentQrUploading}>
                      选择图片
                    </Button>
                  </Upload>
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<UploadOutlined />}
                      disabled={!paymentQrFile}
                      loading={paymentQrUploading}
                      onClick={() => void uploadPaymentQr()}
                    >
                      {extraTarget?.payment_qr_attachment_id ? "替换收款码" : "上传收款码"}
                    </Button>
                    {extraTarget?.payment_qr_attachment_id && (
                      <Button
                        danger
                        icon={<DeleteOutlined />}
                        disabled={paymentQrUploading}
                        onClick={() =>
                          Modal.confirm({
                            title: "移除收款码？",
                            content: "移除后，财务结款信息中的收款码将不可再查看。",
                            okText: "确认移除",
                            okButtonProps: { danger: true },
                            cancelText: "取消",
                            onOk: deletePaymentQr,
                          })
                        }
                      >
                        移除
                      </Button>
                    )}
                  </Space>
                  <Typography.Text type="secondary">
                    支持 JPG、PNG、WebP，单张不超过 10MB；文件存储于私有空间。
                  </Typography.Text>
                </Space>
              </Form.Item>

              <Form.Item label="结款凭证（财务同步）" style={{ marginBottom: 12 }}>
                {extraTarget?.settlement_payment_proof_signed_url ? (
                  <Typography.Link
                    href={extraTarget.settlement_payment_proof_signed_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <EyeOutlined /> 查看结款凭证
                  </Typography.Link>
                ) : (
                  <Typography.Text type="secondary">财务尚未上传结款凭证</Typography.Text>
                )}
              </Form.Item>
            </>
          )}

          {SOURCE_FIELDS.map((f) => (
            <Form.Item key={f.name} name={f.name} label={f.name} style={{ marginBottom: 12 }}>
              {f.name === "颜色及规格" ? (
                <Select
                  allowClear
                  showSearch
                  placeholder={
                    colorSizeOptions.length
                      ? "按货号选择颜色+尺码组合"
                      : "该款暂无SKU，可在商品成本表维护后选择"
                  }
                  options={colorSizeOptions}
                  notFoundContent="该货号下暂无颜色/尺码，请先在商品成本表维护"
                />
              ) : f.type === "select" ? (
                <Select
                  allowClear
                  placeholder={`请选择${f.name}`}
                  options={(f.options ?? []).map((o) => ({ label: o, value: o }))}
                />
              ) : f.type === "number" ? (
                <Input type="number" placeholder={`请输入${f.name}`} allowClear />
              ) : (
                <Input placeholder={`请输入${f.name}`} allowClear />
              )}
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </Card>
  );
}
