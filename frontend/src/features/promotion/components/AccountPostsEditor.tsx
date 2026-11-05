import { useState } from "react";
import { Alert, Card, DatePicker, Form, Input, InputNumber, Modal, Space, Tag, Typography, message } from "antd";
import dayjs, {type Dayjs} from "dayjs";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FieldBlock } from "@/features/flow/FieldBlock";
import { LINE_EVENTS_ALL } from "@/features/flow/api";
import { accountLabel } from "@/features/blogger/accounts";
import { extractErrorMessage } from "@/services/apiClient";
import { replacePlatformPosts } from "../api";
import { disableFutureDate } from "../listConstants";
import type { PlatformPostInput, Promotion } from "../types";

type Values = {rows: {publish_url?: string | null; actual_publish_date?: Dayjs | null; like_count?: number | null}[]};

export function AccountPostsEditor({promotion}: {promotion: Promotion}) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<Values>();
  const qc = useQueryClient();
  const accounts = promotion.account_snapshot?.accounts ?? [];
  const facts = accounts.map(account => ({account, post: promotion.platform_posts?.find(p => p.account_id === account.account_id)}));
  const save = useMutation({
    mutationFn: (rows: PlatformPostInput[]) => replacePlatformPosts(promotion.id, rows),
    onSuccess: () => {
      setOpen(false); message.success("账号发布记录已保存");
      void qc.invalidateQueries({queryKey: ["promotions"]});
      void qc.invalidateQueries({queryKey: LINE_EVENTS_ALL});
    },
    onError: e => message.error(extractErrorMessage(e)),
  });
  const beginEdit = () => {
    form.setFieldsValue({rows: facts.map(({post}) => ({publish_url: post?.publish_url ?? null, actual_publish_date: post?.actual_publish_date ? dayjs(post.actual_publish_date) : null, like_count: post?.like_count ?? null}))});
    setOpen(true);
  };
  const fulfillment = promotion.fulfillment;
  return <>
    <FieldBlock ui={promotion.ui} group="platform_posts" title="约定账号发布" onEdit={beginEdit}>
      <Alert showIcon type={fulfillment?.complete ? "success" : "warning"} style={{marginBottom: 12}}
        message={fulfillment ? `已完成 ${fulfillment.completed} / ${fulfillment.total} 个约定账号发布` : "正在核对账号发布情况"}
        description={<>{fulfillment && !fulfillment.complete && <div>待补：{fulfillment.missing_accounts.map(accountLabel).join("、")}</div>}全部约定账号发布后才可结业务费；独立寄回运费仍按原流程处理。</>} />
      <Typography.Paragraph type="secondary">此处点赞仅作发布记录，报表指标请在主单数据中维护。</Typography.Paragraph>
      <Space direction="vertical" style={{width: "100%"}}>{facts.map(({account, post}) => <Card key={account.account_id} size="small" title={<>{accountLabel(account)}{account.account_id === promotion.account_snapshot?.primary_account_id && <Tag style={{marginLeft: 8}}>主账号</Tag>}</>}>
        <Space wrap><span>发布日期：{post?.actual_publish_date || "待填写"}</span><span>点赞：{post?.like_count ?? "—"}</span></Space>
        <div style={{overflowWrap: "anywhere"}}>{post?.publish_url && /^https?:\/\//i.test(post.publish_url) ? <Typography.Link href={post.publish_url} target="_blank" rel="noreferrer">{post.publish_url}</Typography.Link> : "待填写发布链接"}</div>
      </Card>)}</Space>
    </FieldBlock>
    <Modal title="编辑约定账号发布" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="保存" confirmLoading={save.isPending} width={720} destroyOnHidden>
      <Typography.Paragraph type="secondary">约定账号不能删除。已填写的有效链接和日期可更正；进入结款流程后不能清空必要发布信息。</Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={({rows}) => save.mutate(accounts.map((account, i) => ({account_id: account.account_id, platform: account.platform, publish_url: rows[i]?.publish_url?.trim() || null, actual_publish_date: rows[i]?.actual_publish_date?.format("YYYY-MM-DD") ?? null, like_count: rows[i]?.like_count ?? null})))}>
        {accounts.map((account, i) => {
          const primaryLocked = account.account_id === promotion.account_snapshot?.primary_account_id && promotion.publish_status !== "已发布";
          return <Card key={account.account_id} size="small" title={accountLabel(account)} style={{marginBottom: 12}}>
            {primaryLocked && <Typography.Paragraph type="secondary">主账号请先通过“标记发布”登记，其他账号可先保存发布记录。</Typography.Paragraph>}
            <Form.Item name={["rows", i, "publish_url"]} label="发布链接" rules={[{validator: async (_, value: string | null) => {
              if (!value?.trim()) return;
              try { const url = new URL(value.trim()); if (["http:", "https:"].includes(url.protocol)) return; } catch { /* handled below */ }
              throw new Error("请输入 http 或 https 链接");
            }}]}><Input disabled={primaryLocked} maxLength={512} placeholder="https://" /></Form.Item>
            <Space align="start" wrap>
              <Form.Item name={["rows", i, "actual_publish_date"]} label="发布日期"><DatePicker disabled={primaryLocked} disabledDate={disableFutureDate} /></Form.Item>
              <Form.Item name={["rows", i, "like_count"]} label="点赞"><InputNumber disabled={primaryLocked} min={0} max={2147483647} precision={0} /></Form.Item>
            </Space>
          </Card>;
        })}
      </Form>
    </Modal>
  </>;
}
