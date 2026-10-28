// 报表页筛选状态：URL 参数优先于筛选记忆（7e R1，设计 2.1）。
//
// 初始化顺序 URL → 记忆 → 页面默认。URL 带来的值不写回记忆；用户手动改筛选后才写记忆，
// 并 replace 掉 URL 里本页 schema 的键。默认只接首次进入；BI可接同页的新落点链接。

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Dayjs } from "dayjs";
import dayjs from "dayjs";
import type { FilterPageCode } from "@/features/preference/api";
import { useFilterMemory } from "@/features/preference/useFilterMemory";
import {
  initialFilters,
  parseUrlFilters,
  pickMemoryFilters,
  restoreMemoryFilters,
  schemaUrlKeys,
  splitLandingMemory,
  type ReportFilterSchema,
} from "./reportFilters";

interface Result<T> {
  filters: T;
  /** 记忆已加载并合并完。加载期间不要用 filters 去发业务查询。 */
  ready: boolean;
  /** 用户手动改筛选：合并进状态、写记忆、清掉 URL 里本页的筛选键。 */
  setFilters: (patch: Partial<T>) => void;
}

/**
 * `schema` 与 `defaults` 请用模块级常量；followUrlChanges仅供需要同页链接跳转的页面启用。
 */
export function useReportFilters<T extends object>(
  pageCode: FilterPageCode,
  schema: ReportFilterSchema<T>,
  defaults: T,
  followUrlChanges = false
): Result<T> {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  // 默认只读进入页的URL；BI允许同页落点链接重新覆盖，手动筛选清URL除外。
  const searchKey = searchParams.toString();
  const handledSearchRef = useRef(searchKey);
  const urlFiltersRef = useRef(parseUrlFilters(searchParams, schema));
  const memory = useFilterMemory<T>(pageCode);
  const memoryRef = useRef(memory);
  memoryRef.current = memory;

  const [state, setState] = useState(() => ({
    filters: initialFilters(urlFiltersRef.current, {}, defaults),
    ready: false,
  }));
  const filtersRef = useRef(state.filters);
  filtersRef.current = state.filters;
  const initRef = useRef(false);
  // 落点链接跳过的记忆（splitLandingMemory）：用户没改的键按原值写回
  const keptMemoryRef = useRef<Partial<T>>({});

  useEffect(() => {
    if (followUrlChanges && handledSearchRef.current !== searchKey) {
      handledSearchRef.current = searchKey;
      urlFiltersRef.current = parseUrlFilters(new URLSearchParams(searchKey), schema);
      initRef.current = false;
    }
    if (!memory.ready || initRef.current) return;
    initRef.current = true;
    const { used, kept } = splitLandingMemory(
      urlFiltersRef.current,
      restoreMemoryFilters(memory.restored, schema),
      schema
    );
    keptMemoryRef.current = kept;
    setState({
      filters: initialFilters(urlFiltersRef.current, used, defaults),
      ready: true,
    });
  }, [memory.ready, memory.restored, searchKey, followUrlChanges, schema, defaults]);

  const setFilters = useCallback(
    (patch: Partial<T>) => {
      // 记忆还没回来用户就改了：以用户的选择为准，之后不再用记忆覆盖。
      initRef.current = true;
      const next = { ...filtersRef.current, ...patch };
      filtersRef.current = next;
      setState({ filters: next, ready: true });
      const kept = keptMemoryRef.current;
      for (const k of Object.keys(patch)) delete kept[k as keyof T];
      memoryRef.current.persist({ ...pickMemoryFilters(next, schema), ...kept } as T);
      const keys = schemaUrlKeys(schema);
      if (!keys.some((k) => searchParamsRef.current.has(k))) return;
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          keys.forEach((k) => out.delete(k));
          // 这是用户编辑后的URL清理，不是新的落点链接。
          handledSearchRef.current = out.toString();
          return out;
        },
        { replace: true }
      );
    },
    [schema, setSearchParams]
  );

  return { filters: state.filters, ready: state.ready, setFilters };
}

/** date_from / date_to → RangePicker 的值。 */
export function toDateRange(from?: string, to?: string): [Dayjs, Dayjs] | null {
  return from && to ? [dayjs(from), dayjs(to)] : null;
}

/** RangePicker 的值 → date_from / date_to（清空时两个都置空）。 */
export function fromDateRange(range: [Dayjs, Dayjs] | null): {
  date_from?: string;
  date_to?: string;
} {
  return range
    ? { date_from: range[0].format("YYYY-MM-DD"), date_to: range[1].format("YYYY-MM-DD") }
    : { date_from: undefined, date_to: undefined };
}
