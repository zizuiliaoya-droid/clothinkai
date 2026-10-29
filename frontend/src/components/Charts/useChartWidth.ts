import { useLayoutEffect, useRef, useState } from "react";

/** 首次绘制前测宽，之后沿用ResizeObserver；无布局的测试环境使用原默认宽。 */
export function useChartWidth(fallback: number, enabled = true) {
  const ref = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!enabled || !element) return;
    const update = (width: number) => { if (width > 0) setMeasured(Math.max(240, Math.floor(width))); };
    update(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => update(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [enabled]);
  return { ref, width: enabled ? measured ?? fallback : fallback };
}
