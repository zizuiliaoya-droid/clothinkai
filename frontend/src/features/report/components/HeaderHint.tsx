// 列头说明（7e R4，R3 评审 L4）：Tooltip 挂在列头文字上，不另放问号图标。
//
// 列头文字本身可聚焦（Tab 停一次，聚焦即出 Tooltip，读屏读视觉隐藏的说明）。
// rc-table 横向滚动时会把列头克隆进 aria-hidden 的测量行，克隆出来的那份不能再进 Tab 序列，
// 所以 tabIndex 不写成属性，挂载时按所在位置设：在 aria-hidden 里设 -1，否则 0。
import { Tooltip } from "antd";
/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";
const VISUALLY_HIDDEN = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
} as const;
function setTabStop(el: HTMLSpanElement | null) {
  if (el) el.tabIndex = el.closest('[aria-hidden="true"]') ? -1 : 0;
}
export function HeaderHint({ label, hint }: { label: string; hint: string }) {
  return (
    <Tooltip title={hint} trigger={["hover", "focus"]}>
      <span ref={setTabStop} style={{ cursor: "help", borderBottom: `1px dotted ${MUTED}` }}>
        {label}
        <span style={VISUALLY_HIDDEN}>（{hint}）</span>
      </span>
    </Tooltip>
  );
}
