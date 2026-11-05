import { useCallback, useEffect, useState } from "react";
import { Alert, Button, DatePicker, Form, Modal, Select, Space, Table, Typography, message } from "antd";
import dayjs from "dayjs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BloggerSelect } from "@/components/RemoteSelect/BloggerSelect";
import { useDebouncedValue } from "@/components/RemoteSelect/useDebouncedValue";
import { listGoodsForStyle, listStyles } from "@/features/product/api";
import { useReadOnlySupport } from "@/stores/supportStore";
import { extractErrorMessage } from "@/services/apiClient";
import { AccountSelection } from "./AccountSelection";
import { NegotiationFormModal, type NegotiationFormTarget } from "./NegotiationFormModal";
import { useNegotiationFeedback } from "./useNegotiationFeedback";
import { negotiationGoodsMembers } from "../api";
import { negotiationPayload, type NegotiationFormValues } from "../negotiationForm";
import { BULK_LIMIT, bulkCreate, lookupBulkCreates } from "../bulkApi";
import { draftValues, localResult, mergeCreateResults, resultLabel, type CreateDraftRow } from "../bulkHelpers";
import type { Negotiation } from "../types";

type Props = {open: boolean; onClose: () => void; onEdit: (row: Negotiation) => void};
export function BulkCreateModal({open, onClose, onEdit}: Props) {
  const [form] = Form.useForm<NegotiationFormValues>();
  const qc = useQueryClient();
  const {refresh} = useNegotiationFeedback();
  const readonly = useReadOnlySupport();
  const [groupId, setGroupId] = useState(() => crypto.randomUUID());
  const [rows, setRows] = useState<CreateDraftRow[]>([]);
  const [styles, setStyles] = useState<{value: string; label: string}[]>([]);
  const [search, setSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const keyword = useDebouncedValue(search.trim().slice(0, 64), 300);
  const [busy, setBusy] = useState(false);
  const [bloggerLabel, setBloggerLabel] = useState("");
  const [reference, setReference] = useState<string | null>(null);
  const [editor, setEditor] = useState<{key: string; target: NegotiationFormTarget} | null>(null);
  const ignorePermission = useCallback(() => {}, []);
  const changedAccounts = useCallback(() => setReference(null), []);
  const options = useQuery({queryKey: ["styles", "bulk-picker", keyword], queryFn: () => listStyles({page: 1, page_size: 50, keyword: keyword || undefined}), enabled: open && pickerOpen});
  const pending = rows.some(row => row.uncertain || row.result?.status !== "success");
  useEffect(() => {
    if (!pending) return;
    const warn = (e: BeforeUnloadEvent) => {e.preventDefault(); e.returnValue = "";};
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);
  const updateRow = (key: string, patch: Partial<CreateDraftRow>) => setRows(previous => previous.map(row => row.key === key ? {...row, ...patch} : row));
  async function addRows() {
    if (readonly || busy || !styles.length) return;
    let values: NegotiationFormValues;
    try { values = await form.validateFields(); } catch { return; }
    values = {...form.getFieldsValue(true), ...values};
    if (rows.length + styles.length > BULK_LIMIT) {message.warning("一次最多50行，请减少所选款式"); return;}
    setBusy(true);
    const added = await Promise.all(styles.map(async style => {
      const payload = negotiationPayload({...values, style_id: style.value}, []);
      delete payload.quote_amount;
      const row: CreateDraftRow = {key: crypto.randomUUID(), title: style.label, bloggerLabel, payload, members: []};
      try {
        const goods = await qc.fetchQuery({queryKey: ["goods", "by-style", style.value], queryFn: () => listGoodsForStyle(style.value)});
        if (goods.length > 1) row.preparationError = "请编辑此行，选择单品或套装";
        else {
          const members = await negotiationGoodsMembers(style.value, goods[0]?.goods_main_id);
          row.members = members.members;
          row.payload.goods_main_id = members.goods_main_id;
          row.payload.items = members.members.map(member => ({style_id: member.style_id, sku_id: null}));
          if (goods[0]?.is_suit) row.title = `${goods[0].goods_short_name || goods[0].goods_title}（套装，一单）`;
        }
      } catch (error) { row.preparationError = `商品读取失败，请编辑核对：${extractErrorMessage(error)}`; }
      return row;
    }));
    setRows(previous => [...previous, ...added]); setStyles([]); setBusy(false);
  }
  async function createRows() {
    if (busy || readonly) return;
    setBusy(true);
    let working = rows;
    try {
      const uncertain = working.filter(row => row.uncertain);
      if (uncertain.length) {
        const lookup = await lookupBulkCreates(uncertain.map(row => row.key));
        working = working.map(row => {
          if (!row.uncertain) return row;
          const result = lookup.results.find(r => r.row_key === row.key);
          if (result?.status === "success") return {...row, result, uncertain: false, frozen: undefined};
          if (result?.code === "NOT_CREATED") return {...row, uncertain: false, result: undefined, payload: row.frozen ?? row.payload};
          return {...row, result}; // 查询失败或无结果仍锁定，不能换键重建。
        });
        setRows(working);
      }
      const seenGoods = new Set<string>();
      working = working.map(row => {
        const goods = row.payload.goods_main_id ? `${row.payload.goods_main_id}:${row.payload.blogger_id}:${[...(row.payload.account_ids ?? [row.payload.blogger_id])].sort().join(",")}` : null;
        const duplicate = goods && seenGoods.has(goods);
        if (goods) seenGoods.add(goods);
        if (row.result?.status === "success" || row.uncertain) return row;
        const reason = row.preparationError || (duplicate ? "相同商品/套装已在本组，请保留一行；新轮次另建一组" : null);
        return reason ? {...row, result: localResult(row.key, reason)} : row.result?.code === "NOT_EXECUTED" ? {...row, result: undefined} : row;
      });
      const candidates = working.filter(row => !row.uncertain && row.result?.status !== "success" && !row.preparationError && row.result?.code !== "NOT_EXECUTED");
      setRows(working);
      if (!candidates.length) return;
      const keys = candidates.map(row => row.key);
      working = working.map(row => keys.includes(row.key) ? {...row, frozen: structuredClone(row.payload), uncertain: true, result: undefined} : row);
      setRows(working);
      const response = await bulkCreate(groupId, candidates.map(row => ({client_create_key: row.key, payload: row.payload})));
      setRows(previous => mergeCreateResults(previous, keys, response.results));
      refresh();
    } catch (error) {message.error(`结果暂未核实，请保留此窗口并核对后重试：${extractErrorMessage(error)}`);}
    finally {setBusy(false);}
  }
  function newGroup() {
    if (pending) return;
    setRows([]); setGroupId(crypto.randomUUID());
  }
  return <>
    <Modal title="批量建立谈款草稿" open={open} onCancel={() => {if (!busy) onClose();}} width={1100} footer={<Space wrap>
      <Button disabled={busy} onClick={onClose}>暂时收起</Button>
      <Button disabled={busy || pending || readonly} onClick={newGroup}>开始新一组</Button>
      <Button type="primary" disabled={!rows.length || busy || readonly || !pending} loading={busy} onClick={() => void createRows()}>{rows.some(row => row.uncertain) ? "核对并重试待处理行" : "保存未成功的草稿"}</Button>
    </Space>}>
      <Alert type="info" showIcon message="每行独立保存为草稿，不会自动提交或审核。套装只保留一行，后续仍按主管、老板、收货确认依次处理。" description="成功行不再新建；失败行可修改后重试。结果未收到时先核对，保留本页直到核对完成，刷新会丢失尚未保存的输入。" />
      <Form name="bulk-defaults" form={form} layout="vertical" initialValues={{cooperation_mode: "寄拍", platform: "小红书"}} disabled={busy || readonly} style={{marginTop: 16}}>
        <Form.Item name="blogger_id" label="本组默认博主" rules={[{required: true, message: "请选择博主"}]}><BloggerSelect forNewCooperation onChange={(_, option) => {setBloggerLabel(option?.label ?? "博主"); form.setFieldValue("account_ids", undefined); changedAccounts();}} /></Form.Item>
        <Form.Item name="platform" hidden><Select /></Form.Item>
        <AccountSelection form={form} enabled={open} legacy={false} onSelectionChange={changedAccounts} onReference={setReference} onPermissions={ignorePermission} />
        <Space align="start" wrap>
          <Form.Item name="cooperation_mode" label="默认合作模式" rules={[{required: true}]}><Select style={{width: 150}} options={["寄拍", "送拍", "置换"].map(value => ({value, label: value}))} /></Form.Item>
          <Form.Item name="scheduled_publish_date" label="默认约定发布日"><DatePicker disabledDate={d => d.isBefore(dayjs().startOf("day"))} /></Form.Item>
        </Space>
        <Typography.Paragraph type="secondary">默认值仅用于新增行。每行可单独修改账号、日期和金额；{reference == null ? "当前参考价未知" : `当前参考价 ¥${reference}`}，未手填金额按保存时的具体组合标价确定，明确免费请在该行填0。</Typography.Paragraph>
        <Space.Compact style={{width: "100%", marginBottom: 16}}>
          <Select mode="multiple" labelInValue aria-label="多选款式" placeholder="搜索并多选款式" style={{flex: 1}} filterOption={false} showSearch value={styles}
            onOpenChange={setPickerOpen} onSearch={setSearch} onChange={value => setStyles(value.slice(0, BULK_LIMIT))} loading={options.isFetching}
            options={[...styles, ...(options.data?.items ?? []).filter(style => !styles.some(s => s.value === style.id)).map(style => ({value: style.id, label: `${style.style_code} ${style.style_name}`}))]}
            notFoundContent={options.isError ? "款式读取失败，请重试" : "搜索款号或款名"} />
          <Button disabled={busy || readonly || !styles.length} onClick={() => void addRows()}>生成逐行草稿</Button>
        </Space.Compact>
      </Form>
      <Typography.Paragraph>共 {rows.length} / 50 行；成功 {rows.filter(row => !row.uncertain && row.result?.status === "success").length}，失败 {rows.filter(row => !row.uncertain && row.result?.status === "failed").length}，待核对 {rows.filter(row => row.uncertain).length}。已成功单据从原编辑入口修改。</Typography.Paragraph>
      <Table rowKey="key" dataSource={rows} pagination={false} size="small" scroll={{x: 900, y: 400}} columns={[
        {title: "款项", key: "title", width: 190, render: (_, row) => <>{row.title}<div>{row.bloggerLabel}</div></>},
        {title: "约定日期", key: "date", width: 160, render: (_, row) => <DatePicker aria-label={`${row.title}约定日期`} value={row.payload.scheduled_publish_date ? dayjs(row.payload.scheduled_publish_date) : null} disabled={busy || readonly || row.uncertain || row.result?.status === "success"} disabledDate={d => d.isBefore(dayjs().startOf("day"))} onChange={date => updateRow(row.key, {payload: {...row.payload, scheduled_publish_date: date?.format("YYYY-MM-DD") ?? null}, result: undefined})} />},
        {title: "服务费", key: "amount", width: 160, render: (_, row) => row.result?.negotiation ? (row.result.negotiation.quote_amount == null ? "—" : `¥${row.result.negotiation.quote_amount}`) : row.payload.cooperation_mode === "置换" ? "置换 ¥0" : row.payload.quote_amount == null ? "未手填，按参考标价" : `¥${row.payload.quote_amount}`},
        {title: "逐行结果", key: "result", render: (_, row) => <>{row.preparationError || resultLabel(row.result, row.uncertain)}{row.result?.negotiation_id && <div>单据 ID：{row.result.negotiation_id}</div>}</>},
        {title: "操作", key: "action", width: 170, render: (_, row) => <Space>
          {row.result?.status === "success" ? <Button size="small" disabled={!row.result.negotiation?.ui?.actions?.edit || row.result.negotiation.ui.actions.edit.state !== "enabled" || readonly} onClick={() => {if (row.result?.negotiation) onEdit(row.result.negotiation);}}>编辑已建单</Button> : <>
            <Button size="small" disabled={busy || readonly || row.uncertain} onClick={() => setEditor({key: row.key, target: {editing: null, prepared: {values: draftValues(row.payload), members: row.members, styleLabel: row.title, bloggerLabel: row.bloggerLabel}}})}>编辑此行</Button>
            <Button size="small" disabled={busy || readonly || row.uncertain} onClick={() => setRows(previous => previous.filter(r => r.key !== row.key))}>移除</Button>
          </>}
        </Space>},
      ]} />
    </Modal>
    <NegotiationFormModal target={editor?.target ?? null} onClose={() => setEditor(null)} onPrepare={(payload, _values, members) => {
      if (editor && !readonly) updateRow(editor.key, {payload, members, preparationError: undefined, result: undefined, bloggerLabel: payload.blogger_id === editor.target.prepared?.values.blogger_id ? editor.target.prepared.bloggerLabel : "已调整主账号", title: members.length > 1 ? `${members.map(m => m.display_short_name).join("＋")}（套装，一单）` : members[0]?.display_short_name || "待补款"});
    }} />
  </>;
}
