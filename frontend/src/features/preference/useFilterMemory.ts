// 页面筛选记忆 hook：首次进入回填上次的筛选，之后变更自动保存。

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  getFilterPreference,
  saveFilterPreference,
  type FilterPageCode,
} from "./api";

/** 保存节流：筛选器常被连续拖动/多选，不必每次改动都打一次请求。 */
const SAVE_DEBOUNCE_MS = 600;

interface Result<T> {
  /** 偏好是否已加载完（含"没存过"的情况）。加载期间不要用 filters 去发业务查询。 */
  ready: boolean;
  /** 已回填的筛选值；未加载完时为 undefined。 */
  restored: Partial<T> | undefined;
  /** 供页面在筛选变化后调用。 */
  persist: (filters: T) => void;
}

/**
 * 记忆某个页面的筛选条件。
 *
 * 只负责"读回来"和"写回去"，不接管页面自身的 state —— 页面仍然是筛选值的唯一来源，
 * 这样既不用改动现有筛选逻辑，也避免 hook 和页面各存一份导致的不一致。
 *
 * 读取失败（比如后端未部署该接口）按"没存过"处理：筛选记忆是锦上添花，
 * 不该因为它挂掉就让整个报表页打不开。
 */
export function useFilterMemory<T extends object>(
  pageCode: FilterPageCode,
): Result<T> {
  const [restored, setRestored] = useState<Partial<T> | undefined>(undefined);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data, isSuccess, isError } = useQuery({
    queryKey: ["filter-preference", pageCode],
    queryFn: () => getFilterPreference<T>(pageCode),
    // 偏好只在进入页面时读一次；之后以页面内的 state 为准。
    staleTime: Infinity,
    retry: false,
  });

  useEffect(() => {
    if (isSuccess) setRestored(data ?? {});
    else if (isError) setRestored({});
  }, [isSuccess, isError, data]);

  const saveMutation = useMutation({
    mutationFn: (filters: T) => saveFilterPreference(pageCode, filters),
    // 保存失败不打扰用户：下次变更会再试，丢失的只是"记忆"这一便利。
    onError: () => undefined,
  });

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  function persist(filters: T) {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(
      () => saveMutation.mutate(filters),
      SAVE_DEBOUNCE_MS,
    );
  }

  return { ready: restored !== undefined, restored, persist };
}
