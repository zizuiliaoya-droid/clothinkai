import { describe, expect, it } from "vitest";

import { PLATFORMS } from "./platforms";

describe("PLATFORMS", () => {
  it("与后端 blogger Platform 枚举同序，含得物", () => {
    expect([...PLATFORMS]).toEqual(["小红书", "抖音", "快手", "B站", "得物"]);
  });
});
