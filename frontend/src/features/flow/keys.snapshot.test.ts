// 前后端键表对齐（PR-1 评审 L3）：后端常量导出成 flow_keys.snapshot.json（后端测试守「文件 = 常量」），
// 这里守「文件 = keys.ts / stageStyle.ts」，顺序也比（= 菜单顺序、A ~ H 列顺序）。
// 后端改了常量：在 backend 跑 UPDATE_FLOW_SNAPSHOT=1 pytest tests/unit/test_flow_keys_snapshot.py 重写文件，再改这边。
import { describe, expect, it } from "vitest";
import snapshot from "../../../../backend/app/modules/flow/flow_keys.snapshot.json";
import { ACTION_KEYS, FIELD_KEYS, FLOW_KINDS, PAGE_ACTION_KEYS } from "./keys";
import { PROMOTION_STAGES, PROMOTION_STAGE_COLUMN } from "./stageStyle";

describe("flow 键表与后端快照一致", () => {
  it("单据类型", () => {
    expect([...FLOW_KINDS]).toEqual(snapshot.KINDS);
  });

  it.each([
    ["ACTION_KEYS", ACTION_KEYS, snapshot.ACTION_KEYS],
    ["PAGE_ACTION_KEYS", PAGE_ACTION_KEYS, snapshot.PAGE_ACTION_KEYS],
    ["FIELD_KEYS", FIELD_KEYS, snapshot.FIELD_KEYS],
  ] as const)("%s：每种单据的键与顺序", (_name, ours, theirs) => {
    expect(Object.keys(ours).sort()).toEqual(Object.keys(theirs).sort());
    for (const kind of FLOW_KINDS) {
      expect([...ours[kind]], kind).toEqual(theirs[kind]);
    }
  });

  it("推广单 22 个阶段与列", () => {
    expect([...PROMOTION_STAGES]).toEqual(snapshot.PROMOTION_STAGES);
    expect(Object.keys(PROMOTION_STAGE_COLUMN)).toEqual(Object.keys(snapshot.STAGE_COLUMN));
    expect(PROMOTION_STAGE_COLUMN).toEqual(snapshot.STAGE_COLUMN);
  });

  it("快照不是空壳", () => {
    expect(snapshot.ACTION_KEYS.promotion.length).toBeGreaterThan(0);
    expect(snapshot.FIELD_KEYS.promotion.length).toBeGreaterThan(0);
    expect(snapshot.PROMOTION_STAGES).toHaveLength(22);
  });
});
