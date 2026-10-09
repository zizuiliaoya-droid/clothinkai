// 按 `ui.actions[actionKey]` 渲染一个流程按钮（流程线设计 8.1、8.2）。
//
// - 没有这个键 → 不渲染（矩阵里「隐」）
// - enabled → 可点
// - disabled → 禁用 + 原因：antd 禁用按钮不触发鼠标事件，Tooltip 外包一层 not-allowed 的 span；
//   键盘用户摸不到 Tooltip，showReasonBelow 时在按钮下方用小字直接写原因
// - grey → 禁用 + 灰色提示文字（colorTextSecondary，对白底约 7:1；不用 Typography 的 secondary）

import { useId, type ReactNode } from "react";
import { Button, Tooltip, theme, type ButtonProps } from "antd";
import type { UiAction, UiState } from "./keys";

export interface FlowActionProps {
  ui: Pick<UiState, "actions"> | null | undefined;
  actionKey: string;
  label: ReactNode;
  onClick: (action: UiAction) => void;
  /** disabled 时在按钮下方直接显示原因（抽屉的动作区用）。 */
  showReasonBelow?: boolean;
  buttonProps?: Omit<ButtonProps, "onClick" | "disabled" | "children">;
}

export function FlowAction({ ui, actionKey, label, onClick, showReasonBelow, buttonProps }: FlowActionProps) {
  const { token } = theme.useToken();
  const noteId = useId();
  const action = ui?.actions?.[actionKey];
  if (!action) return null;

  if (action.state === "enabled") {
    return (
      <Button {...buttonProps} onClick={() => onClick(action)}>
        {label}
      </Button>
    );
  }

  const note =
    action.state === "grey"
      ? action.hint
      : (action.reason ?? (action.missing?.length ? `缺：${action.missing.map((m) => m.label).join("、")}` : undefined));
  const showNote = Boolean(note) && (action.state === "grey" || showReasonBelow);

  return (
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
      <Tooltip title={note}>
        <span style={{ cursor: "not-allowed", display: "inline-block" }}>
          <Button {...buttonProps} disabled aria-describedby={showNote ? noteId : undefined}>
            {label}
          </Button>
        </span>
      </Tooltip>
      {showNote && (
        <span id={noteId} style={{ color: token.colorTextSecondary, fontSize: token.fontSizeSM }}>
          {note}
        </span>
      )}
    </span>
  );
}
