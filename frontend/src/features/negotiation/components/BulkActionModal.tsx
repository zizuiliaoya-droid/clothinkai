import { useEffect, useState } from "react";
import { Alert, Button, Card, Input, Modal, Select, Space, Tag, Typography } from "antd";
import { useReadOnlySupport } from "@/stores/supportStore";
import { useAuthStore } from "@/stores/authStore";
import { extractErrorMessage } from "@/services/apiClient";
import { RECEIVER_FIELDS, RECEIVER_LABEL, RECEIVER_MAX_LEN, type ReceiverValues } from "@/features/promotion/components/receiverForm";
import { getNegotiation } from "../api";
import { BULK_ACTION_LABEL, BULK_LIMIT, bulkAction, type BulkAction, type BulkActionPayload, type BulkResult } from "../bulkApi";
import { actionBlock, localResult, needsResultCheck, resultLabel } from "../bulkHelpers";
import { FINAL_RETURN_HINT, MANAGER_REJECT_HINT, RECEIVER_CONFIRM_TEXT, receiverConfirmBody, receiverPrefill, receiverProxyNotice, selfReviewNotice } from "../negotiationDialogs";
import type { Negotiation } from "../types";
import { NegotiationSummary } from "./NegotiationSummary";
import { useNegotiationFeedback } from "./useNegotiationFeedback";

export interface BulkActionTarget {action: BulkAction; rows: Negotiation[]}
interface ActionRow {
  row: Negotiation;
  decision: "approve" | "reject" | "return";
  opinion: string;
  receiver: ReceiverValues;
  note?: string | null;
  result?: BulkResult;
  uncertain?: boolean;
  blocked?: string;
}
function initialRow(row: Negotiation): ActionRow {
  const receiver = receiverPrefill(row);
  return {row, decision: "approve", opinion: "", receiver: receiver.values, note: receiver.note};
}
function inputError(item: ActionRow, action: BulkAction): string | null {
  if ((action === "review" || action === "final-review") && item.decision !== "approve" && !item.opinion.trim()) return "驳回或退回必须填写意见";
  if (action === "confirm-receiver") {
    const body = receiverConfirmBody(item.receiver);
    const missing = RECEIVER_FIELDS.filter(key => !body[key]);
    if (missing.length) return `请填写：${missing.map(key => RECEIVER_LABEL[key]).join("、")}`;
  }
  return null;
}
function payloadOf(item: ActionRow, action: BulkAction): BulkActionPayload {
  if (action === "review") return {action: item.decision === "reject" ? "reject" : "approve", review_opinion: item.opinion.trim() || null};
  if (action === "final-review") return {action: item.decision === "return" ? "return" : "approve", opinion: item.opinion.trim() || null};
  if (action === "confirm-receiver") return receiverConfirmBody(item.receiver);
  return {};
}
export function BulkActionModal({target, onClose}: {target: BulkActionTarget | null; onClose: () => void}) {
  const readonly = useReadOnlySupport();
  const userId = useAuthStore(state => state.user?.id);
  const {refresh} = useNegotiationFeedback();
  const [items, setItems] = useState<ActionRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (!target) return;
    let active = true;
    setNotice(null); setBusy(true);
    setItems(target.rows.slice(0, BULK_LIMIT).map(initialRow));
    // 先读取当前详情和 ui；地址建议也仅从详情取得，不把不同博主共用一套地址。
    void Promise.all(target.rows.slice(0, BULK_LIMIT).map(async row => {
      try { return initialRow(await getNegotiation(row.id)); }
      catch (error) {return {...initialRow(row), blocked: `读取失败，请关闭后重试：${extractErrorMessage(error)}`};}
    })).then(next => {if (active) {setItems(next); setBusy(false);}});
    return () => {active = false;};
  }, [target]);
  const update = (id: string, patch: Partial<ActionRow>) => setItems(previous => previous.map(item => item.row.id === id ? {...item, ...patch, result: undefined} : item));
  async function execute() {
    if (!target || busy || readonly) return;
    setBusy(true); setNotice(null);
    const action = target.action;
    let working = items;
    try {
      // 请求结果丢失后先按已知 ID 核对。进入别的状态不代表本次操作成功。
      working = await Promise.all(working.map(async item => {
        if (!item.uncertain) return item;
        try {
          const current = await getNegotiation(item.row.id);
          if (current.status !== item.row.status) return {...item, row: current, uncertain: false, blocked: "状态已变化，请查看单据时间线核对实际处理人", result: localResult(current.id, "状态已变化，不计为本次成功", "skipped", "STATE_CHANGED")};
          return {...item, row: current, uncertain: false, result: undefined};
        } catch (error) {return {...item, result: localResult(item.row.id, `核对失败：${extractErrorMessage(error)}`, "failed")};}
      }));
      working = working.map(item => {
        if (item.uncertain || item.result?.status === "success" || item.result?.code === "STATE_CHANGED") return item;
        const reason = item.blocked || actionBlock(item.row, action) || inputError(item, action);
        return {...item, result: reason ? localResult(item.row.id, reason) : undefined};
      });
      setItems(working);
      const candidates = working.filter(item => !item.uncertain && !item.result);
      if (!candidates.length) return;
      const ids = candidates.map(item => item.row.id);
      setItems(working.map(item => ids.includes(item.row.id) ? {...item, uncertain: true} : item));
      const response = await bulkAction(action, candidates.map(item => ({negotiation_id: item.row.id, payload: payloadOf(item, action)})));
      setItems(previous => previous.map(item => {
        if (!ids.includes(item.row.id)) return item;
        const result = response.results.find(r => r.row_key === item.row.id);
        return {...item, result, uncertain: needsResultCheck(result)};
      }));
      refresh();
    } catch (error) {setNotice(`结果未收到，请先核对再重试。${extractErrorMessage(error)}`);}
    finally {setBusy(false);}
  }
  function confirm() {
    if (!target || busy || readonly) return;
    if (target.action === "confirm-receiver") Modal.confirm({title: "逐项确认收货信息", content: `${RECEIVER_CONFIRM_TEXT}。只处理本阶段可执行的项目。`, okText: "确认并生成推广单", cancelText: "再看看", onOk: execute});
    else void execute();
  }
  const action = target?.action ?? "submit";
  const success = items.filter(item => item.result?.status === "success").length;
  return <Modal title={`批量${BULK_ACTION_LABEL[action]}`} open={!!target} onCancel={() => {if (!busy) onClose();}} width={850} footer={<Space>
    <Button disabled={busy} onClick={onClose}>关闭</Button>
    <Button type="primary" loading={busy} disabled={readonly || !items.length || success === items.length} onClick={confirm}>{items.some(item => item.uncertain) ? "核对并重试待处理项" : "执行本阶段未成功项"}</Button>
  </Space>}>
    <Alert showIcon type="info" message="逐项按当前权限和阶段处理，不会自动执行下一阶段。成功项不再执行，未执行和失败项保留原因。" description={action === "review" ? MANAGER_REJECT_HINT : action === "final-review" ? FINAL_RETURN_HINT : action === "confirm-receiver" ? "请逐项核对收货信息，确认后各自生成一张推广单。" : "缺少必填信息的谈款，请先从列表编辑补齐。"} />
    {notice && <Alert type="warning" showIcon message={notice} style={{marginTop: 12}} />}
    <Typography.Paragraph style={{marginTop: 12}}>共 {items.length} 项：成功 {success}，未执行 {items.filter(item => !item.uncertain && item.result?.status === "skipped").length}，失败 {items.filter(item => !item.uncertain && item.result?.status === "failed").length}，待核对 {items.filter(item => item.uncertain).length}。</Typography.Paragraph>
    <Space direction="vertical" style={{width: "100%"}}>{items.map(item => {
      const blocked = item.blocked || actionBlock(item.row, action);
      const locked = busy || readonly || !!blocked || item.uncertain || item.result?.status === "success";
      const proxy = action === "confirm-receiver" ? receiverProxyNotice(item.row, userId) : null;
      const selfNotice = item.result?.negotiation ? selfReviewNotice(item.result.negotiation) : null;
      return <Card key={item.row.id} size="small" title={`${item.row.blogger_nickname || "博主"} · ${item.row.display_short_name || item.row.style_name || "待补款"}`}>
        <NegotiationSummary row={item.row} />
        {blocked && <Alert type="warning" message={`未执行：${blocked}`} style={{marginTop: 8}} />}
        {(action === "review" || action === "final-review") && <Space direction="vertical" style={{width: "100%", marginTop: 12}}>
          <Select aria-label={`${item.row.id}审核结果`} value={item.decision} disabled={locked} style={{width: 180}} onChange={decision => update(item.row.id, {decision})}
            options={action === "review" ? [{value: "approve", label: "通过，交老板审核"}, {value: "reject", label: "驳回，结束此单"}] : [{value: "approve", label: "定稿，待收货确认"}, {value: "return", label: "退回修改"}]} />
          <Input.TextArea aria-label={`${item.row.id}审核意见`} value={item.opinion} disabled={locked} maxLength={2000} placeholder={item.decision === "approve" ? "审核意见（可选）" : "请填写驳回或退回意见"} onChange={e => update(item.row.id, {opinion: e.target.value})} />
        </Space>}
        {action === "confirm-receiver" && <Space direction="vertical" style={{width: "100%", marginTop: 12}}>
          {proxy && <Typography.Text>{proxy}</Typography.Text>}{item.note && <Typography.Text type="warning">{item.note}</Typography.Text>}
          {RECEIVER_FIELDS.map(field => <label key={field} style={{display: "block"}}>{RECEIVER_LABEL[field]}
            <Input aria-label={`${item.row.id}${RECEIVER_LABEL[field]}`} value={item.receiver[field]} disabled={locked} maxLength={RECEIVER_MAX_LEN[field]} onChange={e => update(item.row.id, {receiver: {...item.receiver, [field]: e.target.value}})} />
          </label>)}
        </Space>}
        <div style={{marginTop: 12}}><Tag color={item.uncertain ? "warning" : item.result?.status === "success" ? "success" : item.result?.status === "failed" ? "error" : "default"}>{resultLabel(item.result, item.uncertain)}</Tag></div>
        {item.result?.negotiation_id && <Typography.Text type="secondary">单据 ID：{item.result.negotiation_id}</Typography.Text>}
        {item.result?.negotiation?.promotion_internal_code && <div>推广单：{item.result.negotiation.promotion_internal_code}</div>}
        {selfNotice && <Alert type="info" message={selfNotice} />}
      </Card>;
    })}</Space>
  </Modal>;
}
