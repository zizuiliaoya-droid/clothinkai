import { describe, expect, it } from "vitest";
import { restoreProductionFilters, toProductionMemory } from "./productionFilters";

describe("restoreProductionFilters", () => {
  it("回填 preset / season / exclude_brushing，忽略旧记录里的 category（AC 18）", () => {
    const restored = restoreProductionFilters({
      preset: "last_7d",
      season: ["2026秋"],
      category: ["外套"],
      exclude_brushing: false,
    });
    expect(restored).toEqual({
      preset: "last_7d",
      season: ["2026秋"],
      exclude_brushing: false,
    });
    expect("category" in restored).toBe(false);
  });

  it("忽略未知键", () => {
    expect(restoreProductionFilters({ foo: 1, season: [] })).toEqual({ season: [] });
  });

  it("类型不对的值丢弃", () => {
    expect(
      restoreProductionFilters({
        preset: "last_year",
        season: ["2026秋", 3],
        exclude_brushing: "true",
      })
    ).toEqual({});
    expect(restoreProductionFilters({ preset: 7, season: "2026秋" })).toEqual({});
  });

  it("非对象（空、数组、字符串）当作没存过", () => {
    expect(restoreProductionFilters(undefined)).toEqual({});
    expect(restoreProductionFilters(null)).toEqual({});
    expect(restoreProductionFilters(["last_7d"])).toEqual({});
    expect(restoreProductionFilters("last_7d")).toEqual({});
  });
});

describe("toProductionMemory", () => {
  it("只写 preset / season / exclude_brushing", () => {
    const state = {
      preset: "this_month" as const,
      season: ["2026春"],
      exclude_brushing: true,
      category: ["外套"],
    };
    const memory = toProductionMemory(state);
    expect(Object.keys(memory).sort()).toEqual(["exclude_brushing", "preset", "season"]);
    expect(memory).toEqual({ preset: "this_month", season: ["2026春"], exclude_brushing: true });
  });
});
