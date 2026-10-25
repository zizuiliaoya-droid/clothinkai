import { describe, expect, it } from "vitest";
import { parseUrlFilters, pickMemoryFilters, schemaUrlKeys } from "@/features/report/reportFilters";
import { PROMOTION_FILTER_SCHEMA, promotionDimensionParams, promotionIdFromUrl } from "./promotionFilters";

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

describe("推广筛选URL与API合同", () => {
  it("规范URL映射到三个API字段，未分配不传PR UUID", () => {
    const parsed = parseUrlFilters(new URLSearchParams(`goods_id=${ID}&season=春&season=夏&pr_id=unassigned&id=${ID}&page=9`), PROMOTION_FILTER_SCHEMA);
    expect(promotionDimensionParams({ season: [], ...parsed })).toEqual({ goods_main_id: ID, seasons: ["春", "夏"], unassigned_only: true, pr_id: undefined });
    expect(schemaUrlKeys(PROMOTION_FILTER_SCHEMA)).not.toContain("id");
    expect(schemaUrlKeys(PROMOTION_FILTER_SCHEMA)).not.toContain("page");
    expect(pickMemoryFilters({ season: [], ...parsed }, PROMOTION_FILTER_SCHEMA)).toEqual(parsed);
  });
  it("具体PR不被当未分配；坏商品仅丢本键，合法季节保留", () => {
    const parsed = parseUrlFilters(new URLSearchParams(`goods_id=bad&season=春&pr_id=${ID}`), PROMOTION_FILTER_SCHEMA);
    expect(parsed).toEqual({ season: ["春"], pr_id: ID });
    expect(promotionDimensionParams({ season: [], ...parsed })).toMatchObject({ unassigned_only: false, pr_id: ID });
    expect(promotionDimensionParams({ season: [] }).seasons).toBeUndefined();
  });
  it("详情ID严格UUID，大小写归一，未知值不查询", () => {
    expect(promotionIdFromUrl(ID.toUpperCase())).toBe(ID);
    for (const raw of [null, "", "bad", `${ID}/extra`]) expect(promotionIdFromUrl(raw)).toBeUndefined();
  });
});
