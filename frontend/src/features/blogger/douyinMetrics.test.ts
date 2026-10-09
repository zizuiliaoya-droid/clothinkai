import { describe, expect, it } from "vitest";
import snapshot from "./douyinMetrics.snapshot.json";
import {
  DOUYIN_METRIC_FIELDS,
  douyinMetricText,
  usesDouyinMetrics,
} from "./douyinMetrics";

describe("DOUYIN_METRIC_FIELDS", () => {
  it("与后端快照的列名清单逐字一致（含分组前缀的重名列）", () => {
    expect(snapshot.source).toBe("huitun_douyin");
    expect([...DOUYIN_METRIC_FIELDS]).toEqual(snapshot.columns);
    expect(DOUYIN_METRIC_FIELDS).toHaveLength(23);
    expect(DOUYIN_METRIC_FIELDS).toContain("30天视频分析·点赞");
    expect(DOUYIN_METRIC_FIELDS).toContain("7天视频分析·点赞");
  });
});

describe("usesDouyinMetrics", () => {
  it("只有平台筛「抖音」时换成抖音指标列", () => {
    expect(usesDouyinMetrics("抖音")).toBe(true);
    expect(usesDouyinMetrics("小红书")).toBe(false);
    expect(usesDouyinMetrics(undefined)).toBe(false);
    expect(usesDouyinMetrics("")).toBe(false);
  });
});

describe("douyinMetricText", () => {
  const metrics = {
    source: "huitun_douyin",
    raw: { 粉丝总量: "12.3w", 性别分布: "女性居多，占比80.00%", 新增粉丝: "  " },
    values: { 粉丝总量: 123000, 新增点赞: 99 },
  };

  it("读原文 raw，不读解析后的 values", () => {
    expect(douyinMetricText(metrics, "粉丝总量")).toBe("12.3w");
    expect(douyinMetricText(metrics, "性别分布")).toBe("女性居多，占比80.00%");
    // values 里有、raw 里没有 → 不展示数值
    expect(douyinMetricText(metrics, "新增点赞")).toBe("—");
  });

  it("没有快照、没有这一列、空白 → —", () => {
    expect(douyinMetricText(metrics, "新增粉丝")).toBe("—");
    expect(douyinMetricText(metrics, "灰豚指数")).toBe("—");
    expect(douyinMetricText(null, "粉丝总量")).toBe("—");
    expect(douyinMetricText(undefined, "粉丝总量")).toBe("—");
    expect(douyinMetricText({ source: "huitun_douyin" }, "粉丝总量")).toBe("—");
  });
});
