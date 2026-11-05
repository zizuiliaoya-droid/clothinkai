import { useEffect } from "react";
import { Alert, Form, Select, Typography, type FormInstance } from "antd";
import { useQuery } from "@tanstack/react-query";
import { accountLabel, getAccountGroup, resolveAccountQuote, type AccountSnapshot } from "@/features/blogger/accounts";
import { extractErrorMessage } from "@/services/apiClient";
import type { NegotiationFormValues } from "../negotiationForm";

export function AccountSelection({form, enabled, snapshot, legacy, preserveSnapshot = false, onSelectionChange, onReference, onPermissions}: {
  form: FormInstance<NegotiationFormValues>;
  enabled: boolean;
  snapshot?: AccountSnapshot | null;
  legacy: boolean;
  preserveSnapshot?: boolean;
  onSelectionChange: () => void;
  onReference: (amount: string | null) => void;
  onPermissions: (write: boolean) => void;
}) {
  const bloggerId = Form.useWatch("blogger_id", form) as string | undefined;
  const selected = Form.useWatch("account_ids", form) as string[] | undefined;
  const group = useQuery({queryKey: ["blogger-account-group", bloggerId], queryFn: () => getAccountGroup(bloggerId!), enabled: enabled && !!bloggerId});
  const ids = selected?.length ? selected : bloggerId ? [bloggerId] : [];
  const quote = useQuery({queryKey: ["account-quote", bloggerId, [...ids].sort()], queryFn: () => resolveAccountQuote(bloggerId!, ids), enabled: enabled && !!bloggerId && !legacy && !preserveSnapshot && !!group.data});
  useEffect(() => { onPermissions(!!group.data?.can_write_amount); }, [enabled, bloggerId, group.data?.can_write_amount, onPermissions]);
  useEffect(() => {
    if (quote.data) onReference(quote.data.reference_amount);
  }, [quote.data, onReference]);
  useEffect(() => {
    if (legacy || !bloggerId || !group.data) return;
    if (!form.getFieldValue("account_ids")?.length) form.setFieldValue("account_ids", [bloggerId]);
    const primary = preserveSnapshot ? snapshot?.accounts.find(a => a.account_id === bloggerId) : group.data.accounts.find(a => a.id === bloggerId) ?? snapshot?.accounts.find(a => a.account_id === bloggerId);
    if (primary && form.getFieldValue("platform") !== primary.platform) form.setFieldValue("platform", primary.platform);
  }, [group.data, bloggerId, legacy, preserveSnapshot, form, snapshot]);
  if (!bloggerId) return null;
  if (group.isError) return <Alert type="error" showIcon message="账号权限暂时无法读取" description={extractErrorMessage(group.error)} />;
  if (legacy) return null;
  const preview = preserveSnapshot && snapshot ? {
    matched: snapshot.reference_amount != null,
    reference_amount: snapshot.reference_amount,
    can_read_quote: group.data?.can_read_quote ?? false,
  } : quote.data;
  const current = (group.data?.accounts ?? []).map(a => ({value: a.id, label: accountLabel(a), disabled: !a.available_for_new_cooperation && !ids.includes(a.id)}));
  const options = [...current, ...(snapshot?.accounts ?? []).filter(a => !current.some(c => c.value === a.account_id)).map(a => ({value: a.account_id, label: `${accountLabel(a)}（原约定）`, disabled: !ids.includes(a.account_id)}))];
  return <>
    <Form.Item name="account_ids" label="约定合作账号" extra="每个账号均需具备新合作资格；可以选择同平台的多个账号。" rules={[{type: "array", min: 1, max: 10, required: true, message: "请选择1到10个约定账号"}]}>
      <Select mode="multiple" loading={group.isLoading} options={options} onChange={(next: string[]) => {
        onSelectionChange();
        if (next.length && !next.includes(bloggerId)) form.setFieldValue("blogger_id", next[0]);
      }} />
    </Form.Item>
    <Form.Item label="主账号" extra="一张合作单仍计一篇；主账号用于归属和主发布记录。">
      <Select aria-label="主账号" value={bloggerId} options={options.filter(o => ids.includes(o.value))} onChange={id => {onSelectionChange(); form.setFieldValue("blogger_id", id);}} />
    </Form.Item>
    {!preserveSnapshot && quote.isError ? <Alert type="error" showIcon message="暂未取得组合报价" description={extractErrorMessage(quote.error)} /> : !preserveSnapshot && quote.isFetching ? <Typography.Paragraph type="secondary">正在查询本次组合报价…</Typography.Paragraph> : preview && <Alert style={{marginBottom: 12}} showIcon type={preview.matched ? "info" : "warning"}
      message={!preview.can_read_quote ? "当前权限不可查看参考标价" : preview.matched && preview.reference_amount != null ? `${preserveSnapshot ? "约定时参考标价" : "本次参考标价"} ¥${preview.reference_amount}` : preserveSnapshot ? "约定时未设置参考标价，成交金额保持不变" : group.data?.group ? "尚未设置此组合报价，请填写本次成交金额" : "档案尚无报价，请填写本次成交金额"}
      description="成交金额以本次保存为准；未设置组合标价时不会把单账号价格自动相加。" />}
  </>;
}
