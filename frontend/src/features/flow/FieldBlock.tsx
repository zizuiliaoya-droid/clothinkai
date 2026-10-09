// 按 `ui.fields[group]` 渲染一个字段分组（流程线设计 8.1、8.2）。
//
// - 不存在 → 不渲染（矩阵里「隐」，或字段级权限不可读）
// - read → 只读显示 children
// - grey → 灰色提示文字代替值（「发布后填写」）
// - edit → 只读显示 children + 「编辑」按钮（打开对应弹窗）

import type { ReactNode } from "react";
import { Button, Typography, theme } from "antd";
import type { UiState } from "./keys";

export interface FieldBlockProps {
  ui: Pick<UiState, "fields"> | null | undefined;
  group: string;
  title: string;
  children: ReactNode;
  onEdit?: () => void;
}

export function FieldBlock({ ui, group, title, children, onEdit }: FieldBlockProps) {
  const { token } = theme.useToken();
  const field = ui?.fields?.[group];
  if (!field) return null;

  return (
    <section aria-label={title} data-field-group={group}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <Typography.Text strong>{title}</Typography.Text>
        {field.state === "edit" && onEdit && (
          <Button type="link" size="small" onClick={onEdit} aria-label={`编辑${title}`}>
            编辑
          </Button>
        )}
      </div>
      <div>
        {field.state === "grey" ? (
          <span style={{ color: token.colorTextSecondary }}>{field.hint ?? "—"}</span>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
