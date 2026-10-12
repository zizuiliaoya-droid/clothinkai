import { describe, expect, it } from "vitest";
import snapshot from "../../../../backend/app/modules/flow/flow_keys.snapshot.json";
import {
  COMMENT_EMPTY_TEXT,
  EVENT_LABELS,
  attachmentAlt,
  commentError,
  commentImageError,
  describeDiff,
  eventLabel,
  formatItems,
} from "./timeline";

describe("事件文案", () => {
  it("后端每个事件类型都有文案，前端没有多出来的", () => {
    const types = Object.keys(snapshot.EVENT_GROUP);
    expect(types.length).toBeGreaterThan(50);
    expect(Object.keys(EVENT_LABELS).sort()).toEqual([...types].sort());
    for (const t of types) expect(EVENT_LABELS[t], t).toBeTruthy();
  });

  it("文案不重复（时间线上分得清）", () => {
    const labels = Object.values(EVENT_LABELS);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("不认识的类型显示通用文案，不显示类型码", () => {
    expect(eventLabel("foo.bar")).toBe("其他记录");
    expect(attachmentAlt("promotion.payment_qr_uploaded")).toBe("收款码");
    expect(attachmentAlt("line.comment")).toBe("留言贴图");
  });
});

describe("describeDiff", () => {
  it("diff 条目「名：前 → 后」，金额两位、空值显示「空」", () => {
    expect(
      describeDiff({
        diff: {
          receiver_name: { before: null, after: "张三" },
          quote_amount: { before: "300", after: "350.5" },
        },
      })
    ).toEqual(["收件人：空 → 张三", "博主服务费：¥300.00 → ¥350.50"]);
  });

  it("id 类只说已更换，不出值；不认识的键丢掉", () => {
    const lines = describeDiff({
      diff: {
        style_id: { before: "11111111-1111-1111-1111-111111111111", after: "SUIT-1074568657697" },
        goods_items: { before: [["a", "b"]], after: [] },
        secret: { before: 1, after: 2 },
      },
      goods_main_id: "SUIT-1074568657697",
      promotion_id: "p1",
      unknown_key: "SKU-001",
    });
    expect(lines).toEqual(["主款：已更换", "颜色尺码：已更换"]);
    expect(lines.join("")).not.toContain("SUIT-1074568657697");
  });

  it("明细显示「简称 颜色 / 尺码」，系统、金额名单、布尔换成中文", () => {
    expect(
      describeDiff({
        items_before: [["上衣", "黑色", "M"]],
        items_after: [],
        reason: "改颜色尺码明细",
        system: "urge_scan",
        amount_fields: ["quote_amount"],
        by_cancel: true,
      })
    ).toEqual([
      "改前明细：上衣 黑色 / M",
      "改后明细：无",
      "原因：改颜色尺码明细",
      "系统：自动催发扫描",
      "改了金额：博主服务费",
      "因取消推广撤回：是",
    ]);
    expect(formatItems([["上衣", "黑色", "M"], ["短裙", "白色", null]])).toBe("上衣 黑色 / M；短裙 白色");
  });

  it("第一次提交只有 snapshot：列提交时的值（id 类不列）；有 diff 时不再列 snapshot", () => {
    const snap = { blogger_id: "b1", platform: "小红书", quote_amount: "300.00" };
    expect(describeDiff({ snapshot: snap })).toEqual(["平台：小红书", "博主服务费：¥300.00"]);
    expect(describeDiff({ snapshot: snap, diff: { platform: { before: "抖音", after: "小红书" } } })).toEqual([
      "平台：抖音 → 小红书",
    ]);
  });

  it("没有可显示的内容 → 空数组", () => {
    expect(describeDiff({})).toEqual([]);
    expect(describeDiff(null)).toEqual([]);
    expect(describeDiff({ promotion_id: "p1", urge_task_id: "u1" })).toEqual([]);
  });
});

describe("留言校验", () => {
  const img = new File(["x"], "a.png", { type: "image/png" });

  it("文字去空白后与图都空 → 拦", () => {
    expect(commentError("", null)).toBe(COMMENT_EMPTY_TEXT);
    expect(commentError("  \n ", null)).toBe(COMMENT_EMPTY_TEXT);
    expect(commentError("", img)).toBeNull();
    expect(commentError("好的", null)).toBeNull();
    expect(commentError("字".repeat(2001), null)).toContain("2000");
  });

  it("图片类型与大小在选文件时拦", () => {
    expect(commentImageError({ type: "image/jpeg", size: 1024 })).toBeNull();
    expect(commentImageError({ type: "image/gif", size: 1024 })).toContain("JPG");
    expect(commentImageError({ type: "image/png", size: 10 * 1024 * 1024 + 1 })).toContain("10MB");
  });
});
