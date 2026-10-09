import { describe, expect, it, vi } from "vitest";
import { buildMenuItems, type FlowMenuHandlers } from "./flowMenu";
import { ACTION_KEYS, FIELD_KEYS, FLOW_KINDS, PAGE_ACTION_KEYS, type UiState } from "./keys";

function handlers(): FlowMenuHandlers {
  return {
    actions: {
      ship_push: { label: "确认推送仓库", onClick: vi.fn() },
      review: { label: "审核通过", onClick: vi.fn() },
      metrics: { label: "录 7 天数据", onClick: vi.fn() },
      publish: { label: "确认发布", onClick: vi.fn() },
      cancel: { label: "取消推广", onClick: vi.fn(), danger: true },
    },
    edits: {
      receiver: { label: "改收件信息", onClick: vi.fn() },
      payment_qr: { label: "上传收款码", onClick: vi.fn() },
      metrics: { label: "改 7 天数据", onClick: vi.fn() },
    },
  };
}

describe("keys（与后端 flow/matrix.py 的常量一一对应）", () => {
  it("各单据的键数量与 7.1 一致、没有重复", () => {
    const counts = FLOW_KINDS.map((k) => [
      k,
      ACTION_KEYS[k].length,
      PAGE_ACTION_KEYS[k].length,
      FIELD_KEYS[k].length,
    ]);
    expect(counts).toEqual([
      ["negotiation", 8, 1, 8],
      ["promotion", 20, 2, 20],
      ["warehouse", 1, 1, 0],
      ["settlement", 5, 1, 0],
      ["freight", 4, 0, 0],
    ]);
    for (const k of FLOW_KINDS) {
      expect(new Set(ACTION_KEYS[k]).size).toBe(ACTION_KEYS[k].length);
      expect(new Set(FIELD_KEYS[k]).size).toBe(FIELD_KEYS[k].length);
    }
  });
});

describe("buildMenuItems", () => {
  it("顺序按 keys.ts 固定，与接口返回的键顺序无关；动作在前、edits 在后", () => {
    const ui: UiState = {
      actions: {
        review: { state: "enabled" },
        publish: { state: "enabled" },
        ship_push: { state: "enabled" },
      },
      edits: ["payment_qr", "receiver"],
    };
    const keys = buildMenuItems("promotion", ui, handlers()).map((i) => i.key);
    expect(keys).toEqual(["ship_push", "publish", "review", "edit:receiver", "edit:payment_qr"]);
  });

  it("disabled 项禁用，label 带原因；grey 项禁用，label 带提示", () => {
    const ui: UiState = {
      actions: {
        review: { state: "disabled", reason: "需 PR 主管审核" },
        metrics: { state: "grey", hint: "2026-10-15 起可录" },
        ship_push: { state: "enabled" },
      },
    };
    const items = buildMenuItems("promotion", ui, handlers());
    expect(items.map((i) => [i.key, i.label, i.disabled])).toEqual([
      ["ship_push", "确认推送仓库", false],
      ["review", "审核通过（需 PR 主管审核）", true],
      ["metrics", "录 7 天数据（2026-10-15 起可录）", true],
    ]);
  });

  it("disabled 没有 reason 时用 missing 拼「缺：…」", () => {
    const ui: UiState = {
      actions: {
        review: {
          state: "disabled",
          missing: [
            { key: "payment_qr", label: "收款码" },
            { key: "brand_comment", label: "品牌词评论截图" },
          ],
        },
      },
    };
    const [item] = buildMenuItems("promotion", ui, handlers());
    expect(item.label).toBe("审核通过（缺：收款码、品牌词评论截图）");
    expect(item.disabled).toBe(true);
  });

  it("enabled 带 in_dialog 缺项仍可点，点击把整条 UiAction 交给 handler", () => {
    const h = handlers();
    const action = {
      state: "enabled" as const,
      missing: [{ key: "receiver_phone", label: "收件电话" }],
    };
    const [item] = buildMenuItems("promotion", { actions: { ship_push: action } }, h);
    expect(item.disabled).toBe(false);
    item.onClick?.();
    expect(h.actions?.ship_push?.onClick).toHaveBeenCalledWith(action);
  });

  it("禁用项的 onClick 不会触发 handler", () => {
    const h = handlers();
    const [item] = buildMenuItems(
      "promotion",
      { actions: { review: { state: "disabled", reason: "需 PR 主管审核" } } },
      h,
    );
    item.onClick?.();
    expect(h.actions?.review?.onClick).not.toHaveBeenCalled();
  });

  it("不在 ui 里的、没有 handler 的、不认识的键、别的单据的键都不出现", () => {
    const ui: UiState = {
      actions: {
        ship_push: { state: "enabled" },
        ship_withdraw: { state: "enabled" }, // 没有 handler
        teleport: { state: "enabled" }, // 不认识
        manager_review: { state: "enabled" }, // 谈款的键
      },
      edits: ["receiver", "unknown_group"],
    };
    const h = handlers();
    h.actions = { ...h.actions, teleport: { label: "?", onClick: vi.fn() } };
    h.edits = { ...h.edits, unknown_group: { label: "?", onClick: vi.fn() } };
    const keys = buildMenuItems("promotion", ui, h).map((i) => i.key);
    expect(keys).toEqual(["ship_push", "edit:receiver"]);
  });

  it("edits 与同名动作（metrics）不撞 key，点 edits 调 edits 的 handler", () => {
    const h = handlers();
    const items = buildMenuItems(
      "promotion",
      { actions: { metrics: { state: "enabled" } }, edits: ["metrics"] },
      h,
    );
    expect(items.map((i) => i.key)).toEqual(["metrics", "edit:metrics"]);
    items[1].onClick?.();
    expect(h.edits?.metrics?.onClick).toHaveBeenCalledTimes(1);
    expect(h.actions?.metrics?.onClick).not.toHaveBeenCalled();
  });

  it("ui 为空、没有 actions 时返回空数组", () => {
    expect(buildMenuItems("promotion", undefined, handlers())).toEqual([]);
    expect(buildMenuItems("promotion", { actions: {} }, handlers())).toEqual([]);
  });

  it("danger 透传给菜单项", () => {
    const [item] = buildMenuItems("promotion", { actions: { cancel: { state: "enabled" } } }, handlers());
    expect(item.danger).toBe(true);
  });
});
