import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, InputNumber, List, Modal, Select, Space, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { useReadOnlySupport } from "@/stores/supportStore";
import { extractErrorMessage } from "@/services/apiClient";
import { accountLabel, getAccountGroup, saveAccountGroup, saveAccountQuote } from "./accounts";

export function AccountGroupModal({target, onClose}: {target: {id: string; nickname: string} | null; onClose: () => void}) {
  const readOnly = useReadOnlySupport();
  const qc = useQueryClient();
  const [form] = Form.useForm<{name: string; account_ids: string[]}>();
  const [quoteForm] = Form.useForm<{account_ids: string[]; amount: number}>();
  const [candidates, setCandidates] = useState<{value: string; label: string}[]>([]);
  const query = useQuery({queryKey: ["blogger-account-group", target?.id], queryFn: () => getAccountGroup(target!.id), enabled: !!target});
  const data = query.data;
  const refresh = () => {
    void qc.invalidateQueries({queryKey: ["blogger-account-group"]});
    void qc.invalidateQueries({queryKey: ["account-quote"]});
  };
  useEffect(() => {
    if (!data) return;
    form.resetFields();
    if (data.group && data.can_write_quote && !readOnly) quoteForm.resetFields();
    setCandidates([]);
    if (data) form.setFieldsValue({name: data.group?.name ?? target?.nickname, account_ids: data.group?.account_ids ?? data.accounts.map(a => a.id)});
  }, [data, target, form, quoteForm, readOnly]);
  const save = useMutation({
    mutationFn: (values: {name: string; account_ids: string[]}) => saveAccountGroup(data?.group?.id ?? null, values),
    onSuccess: () => {message.success("账号关联已保存，已有合作保持原约定"); refresh();},
    onError: e => message.error(extractErrorMessage(e)),
  });
  const quote = useMutation({
    mutationFn: (values: {account_ids: string[]; amount: number}) => saveAccountQuote(data!.group!.id, {...values, amount: String(values.amount)}),
    onSuccess: () => {message.success("组合标价已保存，已有成交金额不变"); refresh(); quoteForm.resetFields();},
    onError: e => message.error(extractErrorMessage(e)),
  });
  const members = (data?.accounts ?? []).map(a => ({value: a.id, label: accountLabel(a)}));
  const options = [...members, ...candidates.filter(c => !members.some(m => m.value === c.value))];
  const manage = !!data?.can_manage && !readOnly;
  return <Modal title={`${target?.nickname ?? "博主"} · 同人账号与报价`} open={!!target} onCancel={onClose} width={720} footer={<Button onClick={onClose}>关闭</Button>} destroyOnHidden>
    <Alert type="info" showIcon message="关联同一人的账号，可分别设置单账号和多个账号一起合作的标价。已有合作保留当时的账号与成交金额。" />
    {query.isError && <Alert type="error" message={extractErrorMessage(query.error)} />}
    {query.isLoading && <Typography.Paragraph>正在读取账号…</Typography.Paragraph>}
    {data && <>
      <Form name="account-group" form={form} layout="vertical" style={{marginTop: 16}} onFinish={values => save.mutate(values)}>
        <Form.Item name="name" label="同人账号组名称" rules={[{required: true, whitespace: true, message: "请输入名称"}]}><Input maxLength={128} disabled={!manage} /></Form.Item>
        <Form.Item name="account_ids" label="组内账号" rules={[{required: true, type: "array", min: 1, max: 10, message: "请选择1到10个账号"}]}><Select mode="multiple" options={options} disabled={!manage} /></Form.Item>
        {manage && <>
          <Form.Item label="添加已有账号"><BloggerSelect value={undefined} placeholder="搜索并添加同一人的账号" onChange={(id, option) => {
            if (!id) return;
            setCandidates(prev => prev.some(c => c.value === id) ? prev : [...prev, {value: id, label: option?.label ?? "已选账号"}]);
            form.setFieldValue("account_ids", [...new Set([...(form.getFieldValue("account_ids") ?? []), id])]);
          }} /></Form.Item>
          <Button htmlType="submit" loading={save.isPending}>保存账号关联</Button>
        </>}
      </Form>
      <Typography.Title level={5}>具体组合标价</Typography.Title>
      {!data.group ? <Typography.Paragraph type="secondary">尚未关联账号，继续使用该账号的档案报价。关联后可设置组合标价。</Typography.Paragraph> : <>
        {!data.can_read_quote ? <Typography.Paragraph type="secondary">当前权限不可查看标价</Typography.Paragraph> : <List size="small" locale={{emptyText: "尚未设置组合标价"}} dataSource={data.group.quotes} renderItem={item => <List.Item>
          <Space wrap><span>{item.account_ids.map(id => members.find(m => m.value === id)?.label ?? "原组账号").join(" ＋ ")}</span><strong>{item.amount == null ? "无查看权限" : `¥${item.amount}`}</strong></Space>
        </List.Item>} />}
        {!readOnly && data.can_write_quote && <Form name="account-group-quote" form={quoteForm} layout="vertical" onFinish={values => quote.mutate(values)} style={{marginTop: 12}}>
          <Form.Item name="account_ids" label="本次标价适用账号" rules={[{required: true, type: "array", min: 1, max: 10, message: "请选择1到10个具体账号"}]}><Select mode="multiple" options={members} /></Form.Item>
          <Form.Item name="amount" label="组合标价" rules={[{required: true, message: "请输入金额，免费请填0"}]}><InputNumber min={0} precision={2} prefix="¥" style={{width: 200}} /></Form.Item>
          <Button htmlType="submit" loading={quote.isPending}>保存组合标价</Button>
        </Form>}
      </>}
    </>}
  </Modal>;
}
