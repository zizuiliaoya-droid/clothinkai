import { useState, type ReactNode } from "react";
import { ChartFrame } from "./ChartFrame";
import { AXIS_TEXT, CHART_PALETTE, GRID_LINE, defaultFormat, isFiniteNumber } from "./chartUtils";

export interface ScatterDatum {
  key: string;
  label: string;
  x: number | null | undefined;
  y: number | null | undefined;
  /** 点大小；null / 非有限值用固定半径。 */
  size?: number | null;
}

type ValidPoint = ScatterDatum & { x: number; y: number };

interface Props {
  title: string;
  data: ScatterDatum[];
  labelHeader?: string;
  xLabel: string;
  yLabel: string;
  /** 给了才在表格里多一列点大小。 */
  sizeLabel?: string;
  formatX?: (v: number) => string;
  formatY?: (v: number) => string;
  formatSize?: (v: number) => string;
  /** 点击点（只鼠标；键盘走「查看数据」表格）。 */
  onSelect?: (d: ScatterDatum) => void;
  canSelect?: (d: ScatterDatum) => boolean;
  renderLabel?: (d: ScatterDatum) => ReactNode;
  /** 悬停提示，默认「标签：x，y」。 */
  pointTitle?: (d: ScatterDatum) => string;
  emptyText?: string;
  width?: number;
  height?: number;
  color?: string;
}

const PAD_L = 56;
const PAD_R = 20;
const PAD_T = 16;
const PAD_B = 36;
const SCATTER_R_MIN = 4;
const SCATTER_R_MAX = 14;
const SCATTER_R_DEFAULT = 6;

/** 点半径按 √ 映射到 4 ~ 14（面积与数值成正比）；size 为空给固定 6。 */
function radiusOf(size: number | null | undefined, maxSize: number): number {
  if (!isFiniteNumber(size) || size < 0) return SCATTER_R_DEFAULT;
  if (maxSize <= 0) return SCATTER_R_MIN;
  return SCATTER_R_MIN + (SCATTER_R_MAX - SCATTER_R_MIN) * Math.sqrt(size / maxSize);
}

/** 散点图（手写 SVG）：x 或 y 为空 / 非有限值的点不画。 */
export function ScatterChart({
  title,
  data,
  labelHeader = "名称",
  xLabel,
  yLabel,
  sizeLabel,
  formatX = defaultFormat,
  formatY = defaultFormat,
  formatSize = defaultFormat,
  onSelect,
  canSelect,
  renderLabel,
  pointTitle,
  emptyText = "暂无数据",
  width = 640,
  height = 280,
  color = CHART_PALETTE[0],
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const points = data.filter((d): d is ValidPoint => isFiniteNumber(d.x) && isFiniteNumber(d.y));

  const sizes = points.map((p) => p.size).filter((s): s is number => isFiniteNumber(s) && s >= 0);
  const maxSize = Math.max(0, ...sizes);
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(1, ...ys);
  const innerW = width - PAD_L - PAD_R;
  const innerH = height - PAD_T - PAD_B;
  const sx = (v: number) => PAD_L + (maxX === minX ? innerW / 2 : (innerW * (v - minX)) / (maxX - minX));
  const sy = (v: number) => PAD_T + innerH - (innerH * (v - minY)) / (maxY - minY);
  const yTicks = [0, 0.5, 1].map((f) => minY + (maxY - minY) * f);
  const xTicks = maxX === minX ? [minX] : [minX, (minX + maxX) / 2, maxX];

  const columns = [labelHeader, xLabel, yLabel, ...(sizeLabel ? [sizeLabel] : [])];
  return (
    <ChartFrame
      title={title}
      columns={columns}
      rows={points.map((p) => ({
        key: p.key,
        cells: [
          renderLabel ? renderLabel(p) : p.label,
          formatX(p.x),
          formatY(p.y),
          ...(sizeLabel ? [isFiniteNumber(p.size) ? formatSize(p.size) : "—"] : []),
        ],
      }))}
      empty={points.length === 0}
      emptyText={emptyText}
    >
      <svg
        width="100%"
        style={{ maxWidth: width }}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title}：${points.length} 项`}
      >
        {yTicks.map((v, i) => (
          <g key={`y${i}`}>
            <line x1={PAD_L} y1={sy(v)} x2={width - PAD_R} y2={sy(v)} stroke={GRID_LINE} />
            <text x={PAD_L - 6} y={sy(v) + 4} textAnchor="end" fontSize="10" fill={AXIS_TEXT}>
              {formatY(v)}
            </text>
          </g>
        ))}
        {xTicks.map((v, i) => (
          <text
            key={`x${i}`}
            data-testid="x-tick"
            x={sx(v)}
            y={height - PAD_B + 16}
            // 最后一个刻度在右边缘：右对齐，日期不被 viewBox 截掉（评审 r1 L2）
            textAnchor={xTicks.length > 1 && i === xTicks.length - 1 ? "end" : "middle"}
            fontSize="10"
            fill={AXIS_TEXT}
          >
            {formatX(v)}
          </text>
        ))}
        {points.map((p) => {
          const clickable = !!onSelect && (canSelect?.(p) ?? true);
          return (
            <circle
              key={p.key}
              data-testid="point"
              cx={sx(p.x)}
              cy={sy(p.y)}
              r={radiusOf(p.size, maxSize)}
              fill={color}
              fillOpacity={hovered === p.key ? 0.9 : 0.55}
              stroke={color}
              style={{ cursor: clickable ? "pointer" : "default", transition: "fill-opacity 150ms" }}
              onClick={clickable ? () => onSelect?.(p) : undefined}
              onMouseEnter={() => setHovered(p.key)}
              onMouseLeave={() => setHovered(null)}
            >
              <title>{pointTitle ? pointTitle(p) : `${p.label}：${formatX(p.x)}，${formatY(p.y)}`}</title>
            </circle>
          );
        })}
      </svg>
    </ChartFrame>
  );
}
