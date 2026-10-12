// 列头说明（7e R4，R3 评审 L4）：Tooltip 挂在列头文字上，不另放可聚焦的问号图标。
//
// rc-table 横向滚动时会把列头克隆进 aria-hidden 的测量行；列头里有 tabIndex 的元素，
// Tab 就会多停一次。所以这里不设 tabIndex：鼠标悬停看 Tooltip，读屏读视觉隐藏的说明。
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

export function HeaderHint({ label, hint }: { label: string; hint: string }) {
  return (
    <Tooltip title={hint}>
      <span style={{ cursor: "help", borderBottom: `1px dotted ${MUTED}` }}>
        {label}
        <span style={VISUALLY_HIDDEN}>（{hint}）</span>
      </span>
    </Tooltip>
  );
}
