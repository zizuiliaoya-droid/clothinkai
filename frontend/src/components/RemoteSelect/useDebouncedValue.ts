import { useEffect, useState } from "react";

/**
 * 值停止变化 delayMs 之后才跟上（7a-2：下拉搜索防抖，不要每敲一个字发一次请求）。
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
