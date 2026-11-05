import { useState } from "react";
import { Alert, Button, Input, List, Modal, Popconfirm, Select, Space, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import { useReadOnlySupport } from "@/stores/supportStore";
import { extractErrorMessage } from "@/services/apiClient";
import { assignClaims, listClaimPrOptions, listClaims, listWorkWechats, revokeClaim, selfClaim, switchClaimWechat, transferClaim } from "./claims";
import type { Blogger } from "./types";

export function BloggerClaimsModal({targets, onClose}: {targets: Blogger[]; onClose: () => void}) {
  const qc = useQueryClient();
  const readonly = useReadOnlySupport();
  const userId = useAuthStore(s => s.user?.id);
  const [workId, setWorkId] = useState<string>();
  const [prId, setPrId] = useState<string>();
  const [wechats, setWechats] = useState<Record<string, string>>({});
  const [result, setResult] = useState("");
  const manage = targets.some(t => t.can_manage_claims) && !readonly;
  const canSelf = targets.some(t => t.can_self_claim) && !readonly;
  const work = useQuery({queryKey: ["blogger-work-wechats"], queryFn: listWorkWechats});
  const prs = useQuery({queryKey: ["blogger-claim-prs"], queryFn: listClaimPrOptions, enabled: manage});
  const claims = useQuery({queryKey: ["blogger-claims", targets[0]?.id], queryFn: () => listClaims(targets[0].id), enabled: targets.length === 1});
  const refresh = () => { for (const key of ["bloggers", "blogger-claims", "blogger-claim-settings"]) void qc.invalidateQueries({queryKey: [key]}); };
  const action = useMutation({mutationFn: async (run: () => Promise<unknown>) => run(), onSuccess: refresh, onError: e => message.error(extractErrorMessage(e))});
  const workOptions = (work.data?.items ?? []).filter(i => i.is_active).map(i => ({value: i.id, label: i.value}));
  return <Modal title={targets.length === 1 ? `${targets[0].nickname} · 认领` : `批量认领 / 分配（${targets.length} 位）`} open onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>} width={680}>
    <Alert showIcon type="info" message="认领允许多位 PR 同时对接；转交或撤销认领不改变已有业务单负责人。" />
    {!readonly && <Space direction="vertical" style={{width: "100%", marginTop: 16}}>
      <Select aria-label="认领工作微信" placeholder="选择本次使用的主工作微信" value={workId} onChange={setWorkId} options={workOptions} style={{width: "100%"}} />
      {!workOptions.length && <Typography.Text type="secondary">暂无启用账号，请主管在“工作微信”中新增。</Typography.Text>}
      {canSelf && <>
        {targets.map(t => <Input key={t.id} aria-label={`${t.nickname}的登记微信`} placeholder={`${t.nickname}：输入原登记微信以验证认领`} maxLength={64} value={wechats[t.id] ?? ""} onChange={e => setWechats(v => ({...v, [t.id]: e.target.value}))} />)}
        <Button disabled={!workId || targets.some(t => !wechats[t.id]?.trim())} loading={action.isPending} onClick={() => action.mutate(async () => {
          const rows = await selfClaim(targets.map(t => ({blogger_id: t.id, wechat: wechats[t.id].trim(), work_wechat_id: workId!})));
          setResult(rows.map(r => `${targets.find(t => t.id === r.blogger_id)?.nickname}：${r.status === "claimed" ? "认领成功" : r.status === "already_claimed" ? "已认领" : "未通过，请联系主管"}`).join("；"));
          setWechats({});
        })}>验证并认领</Button>
      </>}
      {manage && <Space wrap>
        <Select aria-label="分配给PR" placeholder="选择接收 PR" value={prId} onChange={setPrId} options={(prs.data ?? []).map(p => ({value: p.id, label: p.name}))} style={{width: 210}} />
        <Button disabled={!prId || !workId} loading={action.isPending} onClick={() => action.mutate(async () => {
          const r = await assignClaims(targets.map(t => ({blogger_id: t.id, pr_id: prId!, work_wechat_id: workId!})));
          setResult(`新增认领 ${r.assigned} 条；原有 ${r.existing} 条保持不变。`);
        })}>追加分配</Button>
      </Space>}
      {result && <Alert type="info" message={result} />}
    </Space>}
    {targets.length === 1 && <List header="现有认领" loading={claims.isLoading} dataSource={claims.data ?? []} locale={{emptyText: "暂无认领"}} renderItem={c => <List.Item>
      <Space direction="vertical" style={{width: "100%"}}>
        <span><strong>{c.pr_name}</strong> · {c.work_wechat_name ?? "工作微信待补"}{c.work_wechat_active === false ? "（已停用）" : ""}</span>
        {!readonly && <Space wrap>
          {(manage || (canSelf && c.pr_id === userId)) && <Button size="small" disabled={!workId} loading={action.isPending} onClick={() => action.mutate(() => switchClaimWechat(c.blogger_id, c.pr_id, workId!))}>改用所选工作微信</Button>}
          {manage && <>
            <Popconfirm title="撤销此认领？已有业务仍由原负责人处理。" onConfirm={() => action.mutate(() => revokeClaim(c.blogger_id, c.pr_id))}><Button size="small" danger>撤销</Button></Popconfirm>
            <Popconfirm title="转交给上方所选 PR，并使用所选工作微信？" onConfirm={() => action.mutate(() => transferClaim(c.blogger_id, c.pr_id, prId!, workId!))}><Button size="small" disabled={!prId || !workId || prId === c.pr_id}>转交给所选 PR</Button></Popconfirm>
          </>}
        </Space>}
      </Space>
    </List.Item>} />}
    {claims.isError && <Alert type="error" message={extractErrorMessage(claims.error)} />}
  </Modal>;
}
