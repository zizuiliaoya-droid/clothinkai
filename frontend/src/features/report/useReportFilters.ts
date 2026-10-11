// 报表页筛选状态：URL 参数优先于筛选记忆（7e R1，设计 2.1）。
//
// 初始化顺序 URL → 记忆 → 页面默认。URL 带来的值不写回记忆；用户手动改筛选后才写记忆，
// 并 replace 掉 URL 里本页 schema 的键（别人发来的链接只决定第一次打开时看到什么）。

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
 * `schema` 与 `defaults` 请用模块级常量：只在挂载与记忆加载完时读一次。
 */
export function useReportFilters<T extends object>(
  pageCode: FilterPageCode,
  schema: ReportFilterSchema<T>,
  defaults: T
): Result<T> {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchParamsRef = useRef(searchParams);
  searchParamsRef.current = searchParams;
  // URL 只在进入页面时读一次；之后以页面状态为准。
  const [urlFilters] = useState(() => parseUrlFilters(searchParams, schema));
  const memory = useFilterMemory<T>(pageCode);
  const memoryRef = useRef(memory);
  memoryRef.current = memory;

  const [state, setState] = useState(() => ({
    filters: initialFilters(urlFilters, {}, defaults),
    ready: false,
  }));
  const filtersRef = useRef(state.filters);
  filtersRef.current = state.filters;
  const initRef = useRef(false);

  useEffect(() => {
    if (!memory.ready || initRef.current) return;
    initRef.current = true;
    setState({
      filters: initialFilters(urlFilters, restoreMemoryFilters(memory.restored, schema), defaults),
      ready: true,
    });
    // schema / defaults 是模块常量，urlFilters 只算一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memory.ready, memory.restored]);

  const setFilters = useCallback(
    (patch: Partial<T>) => {
      // 记忆还没回来用户就改了：以用户的选择为准，之后不再用记忆覆盖。
      initRef.current = true;
      const next = { ...filtersRef.current, ...patch };
      filtersRef.current = next;
      setState({ filters: next, ready: true });
      memoryRef.current.persist(pickMemoryFilters(next, schema) as T);
      const keys = schemaUrlKeys(schema);
      if (!keys.some((k) => searchParamsRef.current.has(k))) return;
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          keys.forEach((k) => out.delete(k));
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
