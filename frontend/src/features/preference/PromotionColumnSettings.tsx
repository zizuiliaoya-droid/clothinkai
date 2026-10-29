import { useState } from "react";
import { Alert, Button, Checkbox, Modal, Space, Typography } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, HolderOutlined, SettingOutlined } from "@ant-design/icons";
import { extractErrorMessage } from "@/services/apiClient";
import { orderedColumnKeys, type ColumnOption, type ColumnPreference } from "./columnPreference";

interface Props {
  options: ColumnOption[];
  preference: ColumnPreference;
  loading: boolean;
  loadError: boolean;
  retry: () => void;
  saving: boolean;
  save: (preference: ColumnPreference) => Promise<unknown>;
}

export function PromotionColumnSettings({ options, preference, loading, loadError, retry, saving, save }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(preference);
  const [error, setError] = useState("");
  const keys = options.map((option) => option.key);
  const ordered = orderedColumnKeys(keys, draft);
  const visibleCount = ordered.filter((key) => !draft.hidden.includes(key)).length;

  function move(source: string, target: string) {
    if (source === target || !keys.includes(source) || !keys.includes(target)) return;
    const next = ordered.filter((key) => key !== source);
    next.splice(ordered.indexOf(target), 0, source);
    setDraft((previous) => ({ ...previous, order: next }));
  }

  async function submit() {
    setError("");
    try {
      await save({ order: ordered, hidden: draft.hidden.filter((key) => keys.includes(key)) });
      setOpen(false);
    } catch (failure) {
      setError(extractErrorMessage(failure, "列设置保存失败，请重试"));
    }
  }

  return <>
    <Space wrap>
      <Button aria-label="列设置" icon={<SettingOutlined />} loading={loading} disabled={loadError} onClick={() => {
        setDraft(preference); setError(""); setOpen(true);
      }}>列设置</Button>
      {loadError && <Button type="link" danger onClick={retry}>列设置加载失败，重试</Button>}
    </Space>
    <Modal title="推广列表列设置" open={open} width={480} onCancel={() => { if (!saving) setOpen(false); }}
      onOk={() => void submit()} okText="保存" cancelText="取消" confirmLoading={saving}
      okButtonProps={{ disabled: visibleCount === 0, "aria-label": "保存" }} cancelButtonProps={{ disabled: saving }}>
      <Typography.Paragraph type="secondary">勾选显示的列，拖动调整顺序，也可用上下箭头移动。</Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      <ul aria-label="列顺序" style={{ listStyle: "none", padding: 0, maxHeight: "50vh", overflowY: "auto" }}>
        {ordered.map((key, index) => {
          const label = options.find((option) => option.key === key)!.label;
          return <li key={key} draggable={!saving} onDragStart={(event) => event.dataTransfer.setData("text/plain", key)}
            onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
              event.preventDefault(); if (!saving) move(event.dataTransfer.getData("text/plain"), key);
            }} style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 44, borderBottom: "1px solid #f0f0f0" }}>
            <HolderOutlined aria-hidden style={{ cursor: "grab", color: "#595959" }} />
            <Checkbox checked={!draft.hidden.includes(key)} disabled={saving} onChange={(event) => setDraft((previous) => ({
              ...previous, hidden: event.target.checked ? previous.hidden.filter((item) => item !== key) : [...previous.hidden, key],
            }))} style={{ flex: 1 }}>{label}</Checkbox>
            <Button type="text" icon={<ArrowUpOutlined />} aria-label={`上移${label}`} disabled={saving || index === 0}
              onClick={() => move(key, ordered[index - 1])} />
            <Button type="text" icon={<ArrowDownOutlined />} aria-label={`下移${label}`} disabled={saving || index === ordered.length - 1}
              onClick={() => move(key, ordered[index + 1])} />
          </li>;
        })}
      </ul>
      {!visibleCount && <Typography.Paragraph type="danger">至少保留一列</Typography.Paragraph>}
      <Button disabled={saving} onClick={() => setDraft({ order: [], hidden: [] })}>恢复默认</Button>
    </Modal>
  </>;
}
