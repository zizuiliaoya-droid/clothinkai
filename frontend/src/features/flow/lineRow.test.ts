// @vitest-environment jsdom
// 点一行开主线抽屉（流程线 8.3、8.4）：哪些点击算「点行」、Enter 只认行本身。
import { describe, expect, it, vi } from "vitest";
import type { KeyboardEvent, MouseEvent } from "react";
import { LINE_ROW_IGNORE_CLASS, lineRowProps, shouldOpenFromClick } from "./lineRow";

function buildRow() {
  const table = document.createElement("table");
  const tbody = document.createElement("tbody");
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td class="plain"><span class="text">飞狐短裙</span></td>
    <td class="${LINE_ROW_IGNORE_CLASS}"><span class="ops-text">操作</span></td>
    <td><button type="button"><span class="btn-text">操作</span></button></td>
    <td><a href="#x"><span class="link-text">链接</span></a></td>
    <td><span role="button" class="copy">复制</span></td>`;
  tbody.appendChild(tr);
  table.appendChild(tbody);
  document.body.appendChild(table);
  return tr;
}

describe("shouldOpenFromClick", () => {
  const tr = buildRow();
  const q = (sel: string) => tr.querySelector(sel) as Element;

  it("点普通单元格（含里面的文字）开", () => {
    expect(shouldOpenFromClick(q(".plain"), tr)).toBe(true);
    expect(shouldOpenFromClick(q(".text"), tr)).toBe(true);
  });

  it("点标了忽略的单元格（操作列、主图、hover 卡）不开", () => {
    expect(shouldOpenFromClick(q(`.${LINE_ROW_IGNORE_CLASS}`), tr)).toBe(false);
    expect(shouldOpenFromClick(q(".ops-text"), tr)).toBe(false);
  });

  it("点按钮、链接、role=button 不开", () => {
    expect(shouldOpenFromClick(q(".btn-text"), tr)).toBe(false);
    expect(shouldOpenFromClick(q(".link-text"), tr)).toBe(false);
    expect(shouldOpenFromClick(q(".copy"), tr)).toBe(false);
  });

  it("弹层（挂在 body 上的下拉菜单、看大图）里的点击不开", () => {
    const portal = document.createElement("div");
    portal.innerHTML = `<span class="menu-text">金额改动记录</span>`;
    document.body.appendChild(portal);
    expect(shouldOpenFromClick(portal.querySelector(".menu-text"), tr)).toBe(false);
    expect(shouldOpenFromClick(null, tr)).toBe(false);
  });
});

describe("lineRowProps", () => {
  const tr = buildRow();

  it("可聚焦、点行开、Enter 只在焦点在行本身时开", () => {
    const open = vi.fn();
    const props = lineRowProps(open);
    expect(props.tabIndex).toBe(0);

    props.onClick?.({ target: tr.querySelector(".text"), currentTarget: tr } as unknown as MouseEvent<HTMLElement>);
    expect(open).toHaveBeenCalledTimes(1);
    props.onClick?.({ target: tr.querySelector(".btn-text"), currentTarget: tr } as unknown as MouseEvent<HTMLElement>);
    expect(open).toHaveBeenCalledTimes(1);

    const preventDefault = vi.fn();
    props.onKeyDown?.({ key: "Enter", target: tr, currentTarget: tr, preventDefault } as unknown as KeyboardEvent<HTMLElement>);
    expect(open).toHaveBeenCalledTimes(2);
    expect(preventDefault).toHaveBeenCalled();
    props.onKeyDown?.({
      key: "Enter",
      target: tr.querySelector("button"),
      currentTarget: tr,
      preventDefault,
    } as unknown as KeyboardEvent<HTMLElement>);
    props.onKeyDown?.({ key: " ", target: tr, currentTarget: tr, preventDefault } as unknown as KeyboardEvent<HTMLElement>);
    expect(open).toHaveBeenCalledTimes(2);
  });
});
