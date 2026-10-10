import { describe, expect, it } from "vitest";
import {
  TIME_PRESETS,
  initialFilters,
  parseUrlFilters,
  pickMemoryFilters,
  restoreMemoryFilters,
  schemaUrlKeys,
  type ReportFilterSchema,
} from "./reportFilters";
import type { TimeGranularity, TimePreset } from "./types";

interface Filters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  season: string[];
  pr_id?: string;
  goods_id?: string;
  exclude_brushing: boolean;
  granularity: TimeGranularity;
}

const SCHEMA: ReportFilterSchema<Filters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
  season: { kind: "string_list", url: true, memory: true },
  pr_id: { kind: "pr_id", url: true, memory: false },
  goods_id: { kind: "uuid", url: true, memory: false },
  exclude_brushing: { kind: "boolean", url: true, memory: true },
  // 白名单外的键：url 标了也不从 URL 读
  granularity: { kind: "enum", values: ["day", "week", "month", "year"], url: false, memory: true },
};

const DEFAULTS: Filters = {
  preset: "last_30d",
  season: [],
  exclude_brushing: true,
  granularity: "day",
};

const UUID = "7d3c1f0e-8a2b-4c5d-9e6f-0123456789ab";

const qs = (s: string) => new URLSearchParams(s);

describe("parseUrlFilters", () => {
  it("认白名单里的键并按类型转换", () => {
    expect(
      parseUrlFilters(
        qs(
          `preset=last_7d&season=2026秋&season=2026冬&pr_id=${UUID}&goods_id=${UUID}&exclude_brushing=false`
        ),
        SCHEMA
      )
    ).toEqual({
      preset: "last_7d",
      season: ["2026秋", "2026冬"],
      pr_id: UUID,
      goods_id: UUID,
      exclude_brushing: false,
    });
  });

  it("pr_id=unassigned 表示未分配；goods_id 不认 unassigned", () => {
    expect(parseUrlFilters(qs("pr_id=unassigned&goods_id=unassigned"), SCHEMA)).toEqual({
      pr_id: "unassigned",
    });
  });

  it("格式不对的键单个丢弃，其余照用", () => {
    expect(
      parseUrlFilters(
        qs(
          "preset=last_year&season=2026秋&pr_id=abc&goods_id=1&exclude_brushing=yes&date_from=2026-02-30&date_to=2026-03-01"
        ),
        SCHEMA
      )
    ).toEqual({ season: ["2026秋"] });
  });

  it("不认识的键与白名单外的键（granularity）丢弃", () => {
    expect(parseUrlFilters(qs("foo=1&granularity=week&channel=x"), SCHEMA)).toEqual({});
  });

  it("只带起止日期 → 推定自定义区间", () => {
    expect(parseUrlFilters(qs("date_from=2026-09-01&date_to=2026-09-30"), SCHEMA)).toEqual({
      preset: "custom",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
    });
  });

  it("日期不成对、起大于止、或 custom 没日期 → 时间键整组丢弃", () => {
    expect(parseUrlFilters(qs("date_from=2026-09-01&season=a"), SCHEMA)).toEqual({ season: ["a"] });
    expect(parseUrlFilters(qs("date_from=2026-09-30&date_to=2026-09-01"), SCHEMA)).toEqual({});
    expect(parseUrlFilters(qs("preset=custom"), SCHEMA)).toEqual({});
  });

  it("预设不是 custom 时不带日期", () => {
    expect(
      parseUrlFilters(qs("preset=last_7d&date_from=2026-09-01&date_to=2026-09-30"), SCHEMA)
    ).toEqual({ preset: "last_7d" });
  });

  it("季节超过 20 个、空值或超长 → 季节丢弃", () => {
    const many = Array.from({ length: 21 }, (_, i) => `season=s${i}`).join("&");
    expect(parseUrlFilters(qs(many), SCHEMA)).toEqual({});
    expect(parseUrlFilters(qs("season="), SCHEMA)).toEqual({});
    expect(parseUrlFilters(qs(`season=${"x".repeat(65)}`), SCHEMA)).toEqual({});
  });
});

describe("restoreMemoryFilters", () => {
  it("只认 memory 标记的键，类型不对单个丢弃", () => {
    expect(
      restoreMemoryFilters(
        {
          preset: "last_7d",
          season: ["2026秋", 3],
          exclude_brushing: "true",
          granularity: "week",
          pr_id: UUID,
          category: ["外套"],
        },
        SCHEMA
      )
    ).toEqual({ preset: "last_7d", granularity: "week" });
  });

  it("空数组是有效记忆（用户清空过季节）", () => {
    expect(restoreMemoryFilters({ season: [] }, SCHEMA)).toEqual({ season: [] });
  });

  it("非对象当作没存过", () => {
    expect(restoreMemoryFilters(undefined, SCHEMA)).toEqual({});
    expect(restoreMemoryFilters(null, SCHEMA)).toEqual({});
    expect(restoreMemoryFilters(["last_7d"], SCHEMA)).toEqual({});
  });

  it("记了日期的页：日期不成对丢弃日期", () => {
    const schema: ReportFilterSchema<Pick<Filters, "preset" | "date_from" | "date_to">> = {
      preset: SCHEMA.preset,
      date_from: { kind: "date", url: true, memory: true },
      date_to: { kind: "date", url: true, memory: true },
    };
    expect(
      restoreMemoryFilters(
        { preset: "custom", date_from: "2026-09-01", date_to: "2026-09-30" },
        schema
      )
    ).toEqual({ preset: "custom", date_from: "2026-09-01", date_to: "2026-09-30" });
    expect(restoreMemoryFilters({ preset: "custom", date_from: "2026-09-01" }, schema)).toEqual({
      preset: "custom",
    });
  });
});

describe("initialFilters", () => {
  it("URL → 记忆 → 默认，逐键覆盖", () => {
    expect(
      initialFilters<Filters>(
        { season: ["URL"] },
        { season: ["记忆"], preset: "last_7d", granularity: "week" },
        DEFAULTS
      )
    ).toEqual({ ...DEFAULTS, season: ["URL"], preset: "last_7d", granularity: "week" });
  });

  it("URL 的自定义区间盖过记忆的预设", () => {
    const url = parseUrlFilters<Filters>(qs("date_from=2026-09-01&date_to=2026-09-30"), SCHEMA);
    expect(initialFilters<Filters>(url, { preset: "last_7d" }, DEFAULTS)).toEqual({
      ...DEFAULTS,
      preset: "custom",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
    });
  });
});

describe("pickMemoryFilters / schemaUrlKeys", () => {
  it("只写 memory 标记的键", () => {
    const memory = pickMemoryFilters<Filters>(
      { ...DEFAULTS, date_from: "2026-09-01", date_to: "2026-09-30", pr_id: UUID },
      SCHEMA
    );
    expect(Object.keys(memory).sort()).toEqual([
      "exclude_brushing",
      "granularity",
      "preset",
      "season",
    ]);
  });

  it("URL 键 = url 标记且在白名单里", () => {
    expect(schemaUrlKeys(SCHEMA).sort()).toEqual([
      "date_from",
      "date_to",
      "exclude_brushing",
      "goods_id",
      "pr_id",
      "preset",
      "season",
    ]);
  });
});
