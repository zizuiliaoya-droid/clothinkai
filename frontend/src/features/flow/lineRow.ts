// 列表「点一行开主线抽屉」（流程线设计 6.6、8.3、8.4、8.9）。推广列表、谈款页共用。
//
// - 点行里的交互元素（按钮、链接、输入框、菜单项）或标了 LINE_ROW_IGNORE_CLASS 的单元格（操作列、主图、hover 卡）不开
// - 弹层（下拉菜单、看大图、弹窗）挂在 body 上，React 事件仍会冒泡到行：DOM 上不在这一行里的点击一律不算
// - 行 tabIndex=0、Enter 打开（只认焦点在行本身时，免得行里按钮的 Enter 也开）；关闭后焦点回到行由 LineDrawer 负责
import type { HTMLAttributes, KeyboardEvent, MouseEvent } from "react";
import "./lineRow.css";

/** 列表行的 className（光标、键盘焦点框）。 */
export const LINE_ROW_CLASS = "line-row";
/** 标在不该开抽屉的单元格上（Table 列的 className）。 */
export const LINE_ROW_IGNORE_CLASS = "line-row-ignore";

const INTERACTIVE = [
  "a",
  "button",
  "input",
  "textarea",
  "select",
  "label",
  '[role="button"]',
  '[role="menuitem"]',
  '[role="checkbox"]',
  '[role="combobox"]',
  `.${LINE_ROW_IGNORE_CLASS}`,
].join(", ");

/** 这次点击该不该开抽屉。 */
export function shouldOpenFromClick(target: EventTarget | null, row: Element): boolean {
  if (!(target instanceof Element) || !row.contains(target)) return false;
  const hit = target.closest(INTERACTIVE);
  return !(hit && row.contains(hit));
}

/** Table `onRow` 的返回值：点行 / Enter 调 `open`。 */
export function lineRowProps(open: () => void): HTMLAttributes<HTMLElement> {
  return {
    tabIndex: 0,
    "aria-haspopup": "dialog",
    onClick: (e: MouseEvent<HTMLElement>) => {
      if (shouldOpenFromClick(e.target, e.currentTarget)) open();
    },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.key === "Enter" && e.target === e.currentTarget) {
        e.preventDefault();
        open();
      }
    },
  };
}
