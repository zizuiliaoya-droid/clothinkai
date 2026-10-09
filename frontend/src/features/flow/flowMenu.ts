// 把接口的 `ui.actions` + `ui.edits` 转成「操作」下拉的菜单项（流程线设计 8.1、8.2）。纯函数，vitest 测。
//
// - 顺序按 keys.ts 的常量固定（先动作、后 edits），与接口 JSON 的键顺序无关
// - 不在 ui 里的、页面没给 handler 的、不认识的键都不出现
// - 禁用项把原因写进 label（「审核通过（需 PR 主管审核）」），键盘用户摸不到 Tooltip
// - edits 的 key 加 `edit:` 前缀，避免与同名动作（推广单的 metrics）撞 key

import { ACTION_KEYS, FIELD_KEYS, type FlowKind, type UiAction, type UiState } from "./keys";

export interface FlowActionHandler {
  label: string;
  /** 拿到整条 UiAction：enabled 带 missing 时弹窗据此标出要补的项。 */
  onClick: (action: UiAction) => void;
  danger?: boolean;
}

export interface FlowEditHandler {
  label: string;
  onClick: () => void;
}

export interface FlowMenuHandlers {
  actions?: Partial<Record<string, FlowActionHandler>>;
  edits?: Partial<Record<string, FlowEditHandler>>;
}

/** 可直接交给 antd Dropdown 的 `menu.items`。 */
export interface FlowMenuItem {
  key: string;
  label: string;
  disabled: boolean;
  danger?: boolean;
  onClick?: () => void;
}

function disabledNote(action: UiAction): string | null {
  if (action.state === "grey") return action.hint ?? null;
  if (action.reason) return action.reason;
  if (action.missing?.length) return `缺：${action.missing.map((m) => m.label).join("、")}`;
  return null;
}

export function buildMenuItems(
  kind: FlowKind,
  ui: Pick<UiState, "actions" | "edits"> | null | undefined,
  handlers: FlowMenuHandlers,
): FlowMenuItem[] {
  const items: FlowMenuItem[] = [];
  if (!ui) return items;

  for (const key of ACTION_KEYS[kind]) {
    const action = ui.actions?.[key];
    const handler = handlers.actions?.[key];
    if (!action || !handler) continue;
    const disabled = action.state !== "enabled";
    const note = disabled ? disabledNote(action) : null;
    items.push({
      key,
      label: note ? `${handler.label}（${note}）` : handler.label,
      disabled,
      ...(handler.danger ? { danger: true } : {}),
      onClick: disabled ? undefined : () => handler.onClick(action),
    });
  }

  const edits = new Set(ui.edits ?? []);
  for (const group of FIELD_KEYS[kind]) {
    const handler = handlers.edits?.[group];
    if (!edits.has(group) || !handler) continue;
    items.push({ key: `edit:${group}`, label: handler.label, disabled: false, onClick: handler.onClick });
  }
  return items;
}
