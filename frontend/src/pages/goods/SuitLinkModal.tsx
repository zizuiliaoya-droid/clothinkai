import { useMemo } from "react";
import { Alert, Form, Input, Modal, Select, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormInstance } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  createPlatformLink,
  goodsDisplayName,
  listPlatformLinks,
  type Goods,
  type PlatformLink,
  type PlatformLinkCreate,
} from "@/features/product/api";
import { PLATFORM_LINK_RISK_TEXT } from "@/features/product/platformLinkRisk";
import { extractErrorMessage, isApiError } from "@/services/apiClient";

const PLATFORMS = ["千牛", "万相台"];
const CHANNELS = ["普通", "直播"];
const FORM_FIELDS = ["platform", "platform_id", "channel", "style_id", "title"] as const;

interface FormValues {
  platform: string;
  platform_id: string;
  channel: string;
  style_id: string;
  title?: string;
}

/** 与后端同一套规范化：从 Excel 复制来的平台 ID 常带前导单引号与首尾空白。 */
function normalizePlatformId(v: string | undefined): string {
  return (v ?? "").trim().replace(/^'+/, "").trim();
}

/** 422 的字段错误（FastAPI 的 details.errors）→ 表单字段错误；对不上表单字段的返回 false。 */
function applyFieldErrors(form: FormInstance<FormValues>, err: unknown): boolean {
  if (!isApiError(err)) return false;
  const errors = (
    err.response.data.details as { errors?: { loc?: unknown[]; msg?: string }[] } | undefined
  )?.errors;
  if (!Array.isArray(errors)) return false;
  const fields = errors
    .map((e) => ({ name: String(e.loc?.[e.loc.length - 1] ?? ""), msg: e.msg ?? "格式不对" }))
    .filter((e): e is { name: (typeof FORM_FIELDS)[number]; msg: string } =>
      (FORM_FIELDS as readonly string[]).includes(e.name)
    );
  if (fields.length === 0) return false;
  form.setFields(fields.map((f) => ({ name: f.name, errors: [f.msg] })));
  return true;
}

interface Props {
  /** 要绑定链接的套装；null 时弹窗关闭。 */
  goods: Goods | null;
  onClose: () => void;
}

/**
 * 套装「绑定链接」（8a-5，§9.2）：在商品页给套装新建一条平台链接。
 *
 * 只能挂在套装的成员款式上（后端 `_resolve_goods` 兜底）。提交前二次确认，确认框取消不发请求；
 * 平台 ID 已有链接时后端 409、告知它归属的商品，不覆盖——改归属仍去平台链接页。
 */
export function SuitLinkModal({ goods, onClose }: Props) {
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const members = useMemo(() => (goods?.items ?? []).filter((i) => i.is_active), [goods]);
  const displayName = goods ? goodsDisplayName(goods.goods_title, goods.short_name) : "";

  const { data: links, isLoading: linksLoading } = useQuery({
    queryKey: ["platform-links", "by-goods", goods?.id],
    enabled: !!goods,
    queryFn: () => listPlatformLinks({ goods_main_id: goods!.id, page_size: 100 }),
  });

  const createMutation = useMutation({
    mutationFn: (payload: PlatformLinkCreate) => createPlatformLink(payload),
    onSuccess: () => {
      message.success("链接已绑定，报表会把这条链接的销售算到这个套装上");
      void qc.invalidateQueries({ queryKey: ["goods"] });
      void qc.invalidateQueries({ queryKey: ["platform-links"] });
      onClose();
    },
    onError: (err) => {
      // 409：后端 message 已写明平台 ID 归属哪个商品；422：尽量落到对应字段上
      if (!applyFieldErrors(form, err)) message.error(extractErrorMessage(err));
    },
  });

  function submit(values: FormValues) {
    if (!goods) return;
    const platformId = normalizePlatformId(values.platform_id);
    const payload: PlatformLinkCreate = {
      platform: values.platform,
      platform_id: platformId,
      style_id: values.style_id,
      goods_main_id: goods.id,
      channel: values.channel,
      title: values.title?.trim() || null,
    };
    const same = (links?.items ?? []).find(
      (l) => l.is_active && l.platform === values.platform && l.channel === values.channel
    );
    Modal.confirm({
      title: `确认把这个平台 ID 绑定到「${displayName}」？`,
      okText: "确认绑定",
      okType: "danger",
      cancelText: "取消",
      content: (
        <>
          <Typography.Paragraph>{PLATFORM_LINK_RISK_TEXT}。</Typography.Paragraph>
          {same && (
            <Typography.Paragraph type="warning">
              这个套装已经有一条 {same.platform}
              {same.channel}链接（{same.platform_id}）。业务上一个商品只会有一个千牛链接，确认还要再绑一条吗？
            </Typography.Paragraph>
          )}
        </>
      ),
      // 只有点了确定才发请求；取消什么都不发（AC 33）
      onOk: () => createMutation.mutate(payload),
    });
  }

  const columns: ColumnsType<PlatformLink> = [
    { title: "平台", dataIndex: "platform", width: 80 },
    { title: "平台ID", dataIndex: "platform_id", width: 150 },
    { title: "渠道", dataIndex: "channel", width: 70 },
    {
      title: "关联款式",
      dataIndex: "style_code",
      render: (code: string | null, row) =>
        code ? `${code} ${row.style_name ?? ""}`.trim() : "—",
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 70,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
  ];

  return (
    <Modal
      title={goods ? `绑定链接 · ${displayName}` : "绑定链接"}
      open={!!goods}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="绑定"
      confirmLoading={createMutation.isPending}
      destroyOnHidden
      width={640}
    >
      <Alert type="warning" showIcon message={`${PLATFORM_LINK_RISK_TEXT}。`} style={{ marginBottom: 16 }} />

      <Typography.Text strong>已绑的链接</Typography.Text>
      <Table
        rowKey="id"
        size="small"
        style={{ margin: "8px 0 16px" }}
        loading={linksLoading}
        columns={columns}
        dataSource={links?.items ?? []}
        pagination={false}
        locale={{ emptyText: "还没有链接" }}
      />

      <Form
        key={goods?.id}
        form={form}
        layout="vertical"
        onFinish={submit}
        initialValues={{
          platform: PLATFORMS[0],
          channel: CHANNELS[0],
          style_id: members[0]?.style_id,
        }}
      >
        <Form.Item name="platform" label="平台" rules={[{ required: true, message: "请选平台" }]}>
          <Select options={PLATFORMS.map((p) => ({ label: p, value: p }))} />
        </Form.Item>
        <Form.Item
          name="platform_id"
          label="平台 ID"
          tooltip="千牛商品ID / 万相台主体ID；从 Excel 复制带的前导单引号与首尾空格会自动去掉"
          rules={[
            {
              validator: async (_, v: string | undefined) => {
                const cleaned = normalizePlatformId(v);
                if (!cleaned) throw new Error("请填平台 ID");
                if (cleaned.length > 64) throw new Error("平台 ID 最多 64 个字符");
              },
            },
          ]}
        >
          <Input placeholder="如 1074568657697" allowClear />
        </Form.Item>
        <Form.Item
          name="channel"
          label="渠道"
          tooltip="直播链接的 GMV 单独核算，不计入店铺总销售额。"
        >
          <Select options={CHANNELS.map((c) => ({ label: c, value: c }))} />
        </Form.Item>
        <Form.Item
          name="style_id"
          label="关联款式"
          tooltip="仓库按它发货；只能在这个套装的成员里选"
          rules={[{ required: true, message: "请选关联款式" }]}
        >
          <Select
            options={members.map((m) => ({
              label: `${m.style_code ?? ""} ${m.style_name ?? ""}`.trim() || m.style_id,
              value: m.style_id,
            }))}
          />
        </Form.Item>
        <Form.Item name="title" label="平台标题" rules={[{ max: 255, message: "最多 255 个字符" }]}>
          <Input placeholder="平台侧商品标题（可选）" allowClear />
        </Form.Item>
      </Form>
    </Modal>
  );
}
