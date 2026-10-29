import { useState, type ReactNode } from "react";
import { ChartFrame } from "./ChartFrame";
import { useChartWidth } from "./useChartWidth";
import {
  AXIS_TEXT,
  CHART_PALETTE,
  GRID_LINE,
  defaultFormat,
  isFiniteNumber,
  truncateLabel,
} from "./chartUtils";

export interface BarDatum {
  key: string;
  label: string;
  value: number | null | undefined;
}

type ValidBar = BarDatum & { value: number };

interface Props {
  title: string;
  data: BarDatum[];
  /** 表格的标签列头，默认「名称」。 */
  labelHeader?: string;
  /** 表格的数值列头。 */
  valueLabel: string;
  formatValue?: (v: number) => string;
  /** 点击柱子（只鼠标；键盘走「查看数据」表格里的 renderLabel）。 */
  onSelect?: (d: BarDatum) => void;
  /** 某一项能不能点，默认都能点（有 onSelect 时）。 */
  canSelect?: (d: BarDatum) => boolean;
  /** 表格标签单元格的渲染（下钻链接 / 带提示的文本）。 */
  renderLabel?: (d: BarDatum) => ReactNode;
  emptyText?: string;
  width?: number;
  color?: string;
}

const ROW_H = 28;
const PAD_L = 150;
const PAD_R = 72;
const PAD_T = 8;
const PAD_B = 8;

/** 横向柱图（手写 SVG，同 MiniLineChart 风格）：滤掉空值，正负值以 0 为基线。 */
export function BarChart({
  title,
  data,
  labelHeader = "名称",
  valueLabel,
  formatValue = defaultFormat,
  onSelect,
  canSelect,
  renderLabel,
  emptyText = "暂无数据",
  width: fallbackWidth = 640,
  color = CHART_PALETTE[0],
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const { ref, width } = useChartWidth(fallbackWidth);
  const bars = data.filter((d): d is ValidBar => isFiniteNumber(d.value));
  const selectable = (d: BarDatum) => !!onSelect && (canSelect ? canSelect(d) : true);

  const height = PAD_T + PAD_B + bars.length * ROW_H;
  const innerW = width - PAD_L - PAD_R;
  const minV = Math.min(0, ...bars.map((b) => b.value));
  const maxV = Math.max(0, ...bars.map((b) => b.value));
  const span = maxV - minV || 1;
  const x = (v: number) => PAD_L + (innerW * (v - minV)) / span;
  const zeroX = x(0);

  return (
    <div ref={ref} style={{ width: "100%", minWidth: 0 }}>
    <ChartFrame
      title={title}
      columns={[labelHeader, valueLabel]}
      rows={bars.map((b) => ({
        key: b.key,
        cells: [renderLabel ? renderLabel(b) : b.label, formatValue(b.value)],
      }))}
      empty={bars.length === 0}
      emptyText={emptyText}
    >
      <svg
        width={width}
        height={height}
        style={{ display: "block" }}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title}：${bars.length} 项`}
      >
        <line x1={zeroX} y1={PAD_T} x2={zeroX} y2={height - PAD_B} stroke={GRID_LINE} />
        {bars.map((b, i) => {
          const y = PAD_T + i * ROW_H;
          const left = x(Math.min(0, b.value));
          const barW = Math.max(1, x(Math.max(0, b.value)) - left);
          const clickable = selectable(b);
          const valueText = formatValue(b.value);
          return (
            <g
              key={b.key}
              data-testid="bar"
              style={{ cursor: clickable ? "pointer" : "default" }}
              onClick={clickable ? () => onSelect?.(b) : undefined}
              onMouseEnter={() => setHovered(b.key)}
              onMouseLeave={() => setHovered(null)}
            >
              <title>{`${b.label}：${valueText}`}</title>
              <text x={PAD_L - 8} y={y + ROW_H / 2 + 4} textAnchor="end" fontSize="12" fill={AXIS_TEXT}>
                {truncateLabel(b.label, 11)}
              </text>
              <rect
                x={left}
                y={y + 5}
                width={barW}
                height={ROW_H - 10}
                rx={3}
                fill={color}
                opacity={hovered === b.key ? 1 : 0.8}
                style={{ transition: "opacity 150ms" }}
              />
              {/* 数值统一写在 0 基线右侧，负值也不压到左边的标签 */}
              <text x={Math.max(zeroX, left + barW) + 4} y={y + ROW_H / 2 + 4} fontSize="11" fill={AXIS_TEXT}>
                {valueText}
              </text>
            </g>
          );
        })}
      </svg>
    </ChartFrame>
    </div>
  );
}
