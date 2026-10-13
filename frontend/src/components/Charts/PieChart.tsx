import { useState, type ReactNode } from "react";
import { ChartFrame } from "./ChartFrame";
import { AXIS_TEXT, CHART_PALETTE, defaultFormat, isFiniteNumber, truncateLabel } from "./chartUtils";

export interface PieDatum {
  key: string;
  label: string;
  value: number | null | undefined;
}

type ValidSlice = PieDatum & { value: number };

interface Props {
  title: string;
  data: PieDatum[];
  labelHeader?: string;
  valueLabel: string;
  formatValue?: (v: number) => string;
  /** 点击扇区或图例（只鼠标；键盘走「查看数据」表格）。 */
  onSelect?: (d: PieDatum) => void;
  /** 某一项能不能点（如「其他」不能点），默认都能点（有 onSelect 时）。 */
  canSelect?: (d: PieDatum) => boolean;
  renderLabel?: (d: PieDatum) => ReactNode;
  emptyText?: string;
  size?: number;
}

function percentText(value: number, total: number): string {
  return `${((value / total) * 100).toFixed(1)}%`;
}

/** 饼图（手写 SVG）：滤掉空值、非有限值与 ≤ 0 的项，占比按剩下的合计算。 */
export function PieChart({
  title,
  data,
  labelHeader = "名称",
  valueLabel,
  formatValue = defaultFormat,
  onSelect,
  canSelect,
  renderLabel,
  emptyText = "暂无数据",
  size = 220,
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const slices = data.filter((d): d is ValidSlice => isFiniteNumber(d.value) && d.value > 0);
  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const selectable = (d: PieDatum) => !!onSelect && (canSelect ? canSelect(d) : true);

  const r = size / 2 - 4;
  const c = size / 2;
  const point = (angle: number) => [c + r * Math.sin(angle), c - r * Math.cos(angle)];
  let start = 0;
  const shapes = slices.map((s, i) => {
    const sweep = (s.value / total) * Math.PI * 2;
    const end = start + sweep;
    const [x1, y1] = point(start);
    const [x2, y2] = point(end);
    const large = sweep > Math.PI ? 1 : 0;
    const d = `M ${c} ${c} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
    start = end;
    return { s, d, color: CHART_PALETTE[i % CHART_PALETTE.length] };
  });

  const handlers = (s: ValidSlice) => {
    const clickable = selectable(s);
    return {
      style: { cursor: clickable ? "pointer" : "default" },
      onClick: clickable ? () => onSelect?.(s) : undefined,
      onMouseEnter: () => setHovered(s.key),
      onMouseLeave: () => setHovered(null),
    };
  };

  return (
    <ChartFrame
      title={title}
      columns={[labelHeader, valueLabel, "占比"]}
      rows={slices.map((s) => ({
        key: s.key,
        cells: [renderLabel ? renderLabel(s) : s.label, formatValue(s.value), percentText(s.value, total)],
      }))}
      empty={slices.length === 0}
      emptyText={emptyText}
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-label={`${title}：${slices.length} 项`}
          style={{ flex: "none" }}
        >
          {shapes.map(({ s, d, color }) => {
            const tip = `${s.label}：${formatValue(s.value)}（${percentText(s.value, total)}）`;
            const common = {
              fill: color,
              stroke: "#fff",
              strokeWidth: 1,
              opacity: hovered === null || hovered === s.key ? 1 : 0.6,
            };
            return (
              <g key={s.key} data-testid="slice" {...handlers(s)}>
                <title>{tip}</title>
                {/* 只剩一项时整圆，弧线路径起止点重合会画不出来 */}
                {slices.length === 1 ? (
                  <circle cx={c} cy={c} r={r} {...common} />
                ) : (
                  <path d={d} {...common} style={{ transition: "opacity 150ms" }} />
                )}
              </g>
            );
          })}
        </svg>
        <ul style={{ listStyle: "none", margin: 0, padding: 0, minWidth: 0, flex: "1 1 160px" }}>
          {shapes.map(({ s, color }) => (
            <li
              key={s.key}
              {...handlers(s)}
              style={{
                ...handlers(s).style,
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                color: AXIS_TEXT,
                padding: "2px 0",
              }}
              title={s.label}
            >
              <span
                aria-hidden="true"
                style={{ width: 10, height: 10, borderRadius: 2, background: color, flex: "none" }}
              />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {truncateLabel(s.label, 14)}
              </span>
              <span style={{ marginLeft: "auto", color: "#0f172a" }}>{percentText(s.value, total)}</span>
            </li>
          ))}
        </ul>
      </div>
    </ChartFrame>
  );
}
