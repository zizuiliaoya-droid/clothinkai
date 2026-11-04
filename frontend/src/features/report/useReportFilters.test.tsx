// @vitest-environment jsdom
import { useSupportStore } from "@/stores/supportStore";
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
afterEach(() => { cleanup(); useSupportStore.getState().end(); });

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

// 7e R6 评审 r1 M1：带落点键（BI 下钻的 goods_id / pr_id）的链接不套记忆里的 URL 键（季节），
// 落点只本次生效，之后改别的筛选时记忆里的季节按原值写回。
const LANDING_SCHEMA: ReportFilterSchema<Filters> = {
  ...SCHEMA,
  pr_id: { kind: "pr_id", url: true, memory: false, landing: true },
};
const LANDING_URL = "/work-progress?preset=custom&date_from=2026-10-01&date_to=2026-10-31&pr_id=unassigned";
const SAVED: Partial<Filters> = { preset: "last_7d", season: ["记忆季"], granularity: "week" };

function setupLanding(url: string) {
  getPrefMock.mockResolvedValue(SAVED);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return renderHook(() => useReportFilters<Filters>("pr_work_progress", LANDING_SCHEMA, DEFAULTS), {
    wrapper,
  });
}

describe("useReportFilters 落点链接", () => {
  it("URL 带落点键：季节回落默认（不套记忆），不能从 URL 读的粒度照旧用记忆", async () => {
    const { result } = setupLanding(LANDING_URL);
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.filters).toEqual({
      preset: "custom",
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      season: [],
      pr_id: "unassigned",
      granularity: "week",
    });
  });

  it("没带落点键：记忆里的季节照常生效", async () => {
    const { result } = setupLanding("/work-progress?preset=custom&date_from=2026-10-01&date_to=2026-10-31");
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.filters.season).toEqual(["记忆季"]);
  });

  it("落点后改别的筛选：记忆里的季节按原值写回，不被清空", async () => {
    const { result } = setupLanding(LANDING_URL);
    await waitFor(() => expect(result.current.ready).toBe(true));
    act(() => result.current.setFilters({ granularity: "month" }));
    expect(result.current.filters.season).toEqual([]);
    await waitFor(() => expect(savePrefMock).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(savePrefMock).toHaveBeenCalledWith("pr_work_progress", {
      preset: "custom",
      season: ["记忆季"],
      granularity: "month",
    });
  });

  it("落点后手动改季节：写用户选的季节", async () => {
    const { result } = setupLanding(LANDING_URL);
    await waitFor(() => expect(result.current.ready).toBe(true));
    act(() => result.current.setFilters({ season: ["2026秋"] }));
    await waitFor(() => expect(savePrefMock).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(savePrefMock).toHaveBeenCalledWith(
      "pr_work_progress",
      expect.objectContaining({ season: ["2026秋"] })
    );
  });
});

it("只读排查仍可改变筛选，但不保存客户偏好", async () => {
  useSupportStore.getState().start({ access_token: "support", expires_in: 900, tenant: { id: "a", code: "a", name: "客户 A" } });
  const { result } = setup("/production", {});
  await waitFor(() => expect(result.current.r.ready).toBe(true));
  act(() => result.current.r.setFilters({ season: ["秋冬"], preset: "last_7d" }));
  expect(result.current.r.filters.season).toEqual(["秋冬"]);
  await act(waitDebounce);
  expect(savePrefMock).not.toHaveBeenCalled();
});
