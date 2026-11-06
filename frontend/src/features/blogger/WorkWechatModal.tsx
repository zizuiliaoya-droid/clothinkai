import { useState } from "react";
import { Alert, Button, Input, List, Modal, Space, Tag, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createWorkWechat, listWorkWechats, setWorkWechatActive } from "./claims";
import { extractErrorMessage } from "@/services/apiClient";
import { useReadOnlySupport } from "@/stores/supportStore";

export function WorkWechatModal({open, onClose}: {open: boolean; onClose: () => void}) {
  const [value, setValue] = useState("");
  const qc = useQueryClient();
  const readonly = useReadOnlySupport();
  const query = useQuery({queryKey: ["blogger-work-wechats"], queryFn: listWorkWechats, enabled: open});
  const refresh = () => void qc.invalidateQueries({queryKey: ["blogger-work-wechats"]});
  const create = useMutation({mutationFn: createWorkWechat, onSuccess: () => { setValue(""); refresh(); }, onError: e => message.error(extractErrorMessage(e))});
  const toggle = useMutation({mutationFn: ({id, active}: {id: string; active: boolean}) => setWorkWechatActive(id, active), onSuccess: refresh, onError: e => message.error(extractErrorMessage(e))});
  const manage = query.data?.can_manage && !readonly;
  return <Modal title="工作微信" open={open} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
    <Alert type="info" showIcon message="每条认领使用一个主工作微信；停用后旧认领保留，不能再新选。无需填写密码。" />
    {query.isError && <Alert type="error" message={extractErrorMessage(query.error)} />}
    <List loading={query.isLoading} dataSource={query.data?.items ?? []} renderItem={item => <List.Item actions={manage ? [<Button key="toggle" size="small" loading={toggle.isPending} onClick={() => toggle.mutate({id: item.id, active: !item.is_active})}>{item.is_active ? "停用" : "启用"}</Button>] : []}>
      <Space>{item.value}{!item.is_active && <Tag>已停用</Tag>}</Space>
    </List.Item>} />
    {manage && <Space.Compact style={{width: "100%"}}><Input aria-label="工作微信标识" maxLength={64} placeholder="标准名称或工作微信号" value={value} onChange={e => setValue(e.target.value)} /><Button type="primary" disabled={!value.trim()} loading={create.isPending} onClick={() => create.mutate(value.trim())}>新增</Button></Space.Compact>}
  </Modal>;
}
