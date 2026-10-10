// @vitest-environment jsdom
// useReportFilters（7e R1，设计 2.1）：URL 覆盖记忆、URL 值不写回记忆、坏键单个丢弃、手动改后写记忆并清 URL。
import type { ReactNode } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TIME_PRESETS, type ReportFilterSchema } from "./reportFilters";
import type { TimeGranularity, TimePreset } from "./types";

const getPrefMock = vi.fn();
const savePrefMock = vi.fn();
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));

import { useReportFilters } from "./useReportFilters";

interface Filters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  season: string[];
  pr_id?: string;
  granularity: TimeGranularity;
}

const SCHEMA: ReportFilterSchema<Filters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
  season: { kind: "string_list", url: true, memory: true },
  pr_id: { kind: "pr_id", url: true, memory: false },
  granularity: { kind: "enum", values: ["day", "week", "month", "year"], url: false, memory: true },
};

const DEFAULTS: Filters = { preset: "last_30d", season: [], granularity: "day" };

beforeEach(() => {
  getPrefMock.mockReset();
  savePrefMock.mockReset();
  savePrefMock.mockResolvedValue(undefined);
});
afterEach(cleanup);

function setup(url: string, saved: Partial<Filters> | Error) {
  if (saved instanceof Error) getPrefMock.mockRejectedValue(saved);
  else getPrefMock.mockResolvedValue(saved);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return renderHook(
    () => ({
      r: useReportFilters<Filters>("product_roi", SCHEMA, DEFAULTS),
      search: useLocation().search,
    }),
    { wrapper }
  );
}

/** 等足保存节流（600ms）再看有没有写记忆。 */
const waitDebounce = () => new Promise((resolve) => setTimeout(resolve, 800));

describe("useReportFilters", () => {
  it("URL 覆盖记忆，记忆覆盖默认；记忆加载完才 ready", async () => {
    const { result } = setup("/production?season=URL季&date_from=2026-09-01&date_to=2026-09-30", {
      preset: "last_7d",
      season: ["记忆季"],
      granularity: "week",
    });
    expect(result.current.r.ready).toBe(false);
    await waitFor(() => expect(result.current.r.ready).toBe(true));
    expect(result.current.r.filters).toEqual({
      preset: "custom",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
      season: ["URL季"],
      granularity: "week",
    });
  });

  it("URL 带来的值不写回记忆，URL 也保留", async () => {
    const { result } = setup("/production?preset=last_7d&season=URL季", {});
    await waitFor(() => expect(result.current.r.ready).toBe(true));
    await waitDebounce();
    expect(savePrefMock).not.toHaveBeenCalled();
    const params = new URLSearchParams(result.current.search);
    expect(params.get("preset")).toBe("last_7d");
    expect(params.getAll("season")).toEqual(["URL季"]);
  });

  it("坏键单个丢弃，其余照用；pr_id=unassigned 认作未分配", async () => {
    const { result } = setup("/production?preset=bad&season=好季&pr_id=unassigned&granularity=year", {
      preset: "last_7d",
    });
    await waitFor(() => expect(result.current.r.ready).toBe(true));
    expect(result.current.r.filters).toEqual({
      preset: "last_7d",
      season: ["好季"],
      pr_id: "unassigned",
      granularity: "day",
    });
  });

  it("手动改筛选后写记忆（只写 memory 键），并 replace 掉本页 URL 键、保留其他参数", async () => {
    const { result } = setup(
      "/production?date_from=2026-09-01&date_to=2026-09-30&pr_id=unassigned&keep=1",
      {}
    );
    await waitFor(() => expect(result.current.r.ready).toBe(true));
    act(() => result.current.r.setFilters({ season: ["2026秋"] }));
    expect(result.current.r.filters.season).toEqual(["2026秋"]);
    expect(result.current.search).toBe("?keep=1");
    await waitFor(() => expect(savePrefMock).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(savePrefMock).toHaveBeenCalledWith("product_roi", {
      preset: "custom",
      season: ["2026秋"],
      granularity: "day",
    });
  });

  it("记忆读取失败按没存过：URL → 默认", async () => {
    const { result } = setup("/production?preset=this_month", new Error("404"));
    await waitFor(() => expect(result.current.r.ready).toBe(true));
    expect(result.current.r.filters).toEqual({ ...DEFAULTS, preset: "this_month" });
  });
});
