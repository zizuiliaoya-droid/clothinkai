import { useState } from "react";
import { Button, Input, Modal, Space, Tag, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDictItem,
  deleteDictItem,
  listDictItems,
} from "@/features/product/api";
import { extractErrorMessage } from "@/services/apiClient";

interface Props {
  open: boolean;
  onClose: () => void;
}

const SECTIONS: { type: string; label: string }[] = [
  { type: "category", label: "类目" },
  { type: "season", label: "季节 / 系列" },
  { type: "color", label: "颜色" },
  { type: "size", label: "尺码" },
];

function DictSection({ type, label }: { type: string; label: string }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const { data: items } = useQuery({
    queryKey: ["dict-items", type],
    queryFn: () => listDictItems(type),
  });

  const addMutation = useMutation({
    mutationFn: (v: string) => createDictItem(type, v),
    onSuccess: () => {
      setValue("");
      void qc.invalidateQueries({ queryKey: ["dict-items", type] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const delMutation = useMutation({
    mutationFn: (id: string) => deleteDictItem(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["dict-items", type] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  return (
    <div style={{ marginBottom: 20 }}>
      <Typography.Text strong>{label}</Typography.Text>
      <div style={{ margin: "8px 0" }}>
        {(items ?? []).map((it) => (
          <Tag
            key={it.id}
            closable
            onClose={(e) => {
              e.preventDefault();
              delMutation.mutate(it.id);
            }}
            style={{ marginBottom: 6 }}
          >
            {it.value}
          </Tag>
        ))}
        {(items ?? []).length === 0 ? (
          <Typography.Text type="secondary">暂无</Typography.Text>
        ) : null}
      </div>
      <Space.Compact style={{ width: "100%" }}>
        <Input
          placeholder={`新增${label}值（如 2026春）`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={() => value.trim() && addMutation.mutate(value.trim())}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={addMutation.isPending}
          onClick={() => value.trim() && addMutation.mutate(value.trim())}
        >
          添加
        </Button>
      </Space.Compact>
    </div>
  );
}

/** 类目 / 季节(系列) 字典管理弹窗 —— 增删由租户自维护。 */
export function DictManagerModal({ open, onClose }: Props) {
  return (
    <Modal
      title="管理字典（类目 / 季节 / 颜色 / 尺码）"
      open={open}
      onCancel={onClose}
      onOk={onClose}
      okText="完成"
      cancelButtonProps={{ style: { display: "none" } }}
      destroyOnHidden
      width={520}
    >
      <div style={{ marginTop: 12 }}>
        {SECTIONS.map((s) => (
          <DictSection key={s.type} type={s.type} label={s.label} />
        ))}
      </div>
    </Modal>
  );
}
