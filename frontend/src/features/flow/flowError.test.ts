import { describe, expect, it } from "vitest";
import { CONFLICT_REFRESH_TEXT, PERMISSION_REFRESH_TEXT, flowErrorOutcome } from "./flowError";

function apiErr(status: number, data: Record<string, unknown>) {
  return { response: { status, data } };
}

describe("flowErrorOutcome（8.2）", () => {
  it("422 FLOW_GATE_MISSING → gate，missing 原样带出", () => {
    const missing = [{ key: "receiver_phone", label: "收件电话" }];
    expect(
      flowErrorOutcome(apiErr(422, { code: "FLOW_GATE_MISSING", message: "缺", details: { missing } }))
    ).toEqual({ kind: "gate", missing });
  });

  it("409 → 统一文案并刷新", () => {
    expect(flowErrorOutcome(apiErr(409, { code: "STATE_CONFLICT", message: "x" }))).toEqual({
      kind: "message",
      text: CONFLICT_REFRESH_TEXT,
      refresh: true,
    });
  });

  it("403 PERMISSION_DENIED → 通用文案并刷新", () => {
    expect(flowErrorOutcome(apiErr(403, { code: "PERMISSION_DENIED", message: "无权限" }))).toEqual({
      kind: "message",
      text: PERMISSION_REFRESH_TEXT,
      refresh: true,
    });
  });

  it("403 FLOW_ACTION_FORBIDDEN → 显示 reason，不刷新", () => {
    expect(
      flowErrorOutcome(
        apiErr(403, {
          code: "FLOW_ACTION_FORBIDDEN",
          message: "不允许",
          details: { rule: "self_review", reason: "不能审核自己谈的单" },
        })
      )
    ).toEqual({ kind: "message", text: "不能审核自己谈的单", refresh: false });
  });

  it("其余错误 → 接口 message", () => {
    expect(
      flowErrorOutcome(apiErr(422, { code: "INVALID_RECEIVER_PHONE", message: "收件电话格式不对" }))
    ).toEqual({ kind: "message", text: "收件电话格式不对", refresh: false });
  });
});
