import { Empty } from "antd";
import { isFiniteNumber } from "@/components/Charts/chartUtils";
import { useChartWidth } from "@/components/Charts/useChartWidth";

export interface Series {
  name: string;
  color: string;
  /** null（或 NaN 等非有限值）= 这一天没有值：断线、不画点。 */
  data: (number | null)[];
}

interface Props {
  labels: string[];
  series: Series[];
  height?: number;
  width?: number;
  ariaLabel?: string;
  /** Y 轴刻度的格式化，默认取整加千分位。 */
  formatValue?: (v: number) => string;
  /** 跟着容器宽度画（不超过 width），窄屏不用横向滚动；量不到宽度时（如测试环境）用 width。 */
  fluid?: boolean;
}

/** 横轴一个标签至少占的宽度（「YYYY-MM-DD」10 号字约 50px）。 */
const X_LABEL_MIN_SPACE = 64;

/**
 * 轻量 SVG 折线图（无第三方依赖）：多序列折线 + 网格 + 图例 + 端点。
 * 用于单款投产日趋势（支付金额 / 站内花费）等场景。空值断线，不画成 0。
 */
export function MiniLineChart({
  labels,
  series,
  height = 260,
  width = 720,
  ariaLabel = "投产趋势折线图",
  formatValue = (v) => Math.round(v).toLocaleString(),
  fluid = false,
}: Props) {
  // 空数据时渲染 Empty、没有容器：有数据后再量
  const hasData = labels.length > 0 && series.length > 0;
  const { ref: wrapRef, width: measured } = useChartWidth(width, fluid && hasData);

  if (!hasData) {
    return <Empty description="暂无趋势数据" />;
  }
  const chartWidth = fluid ? Math.min(width, measured) : width;
  const padL = 48;
  const padR = 16;
  const padT = 16;
  const padB = 40;
  const innerW = chartWidth - padL - padR;
  const innerH = height - padT - padB;

  // 每条序列先把空值统一成 null，后面断线、算最大值都只看有限数字
  const cleaned = series.map((s) => ({
    ...s,
    values: s.data.map((v) => (isFiniteNumber(v) ? v : null)),
  }));
  const allVals = cleaned.flatMap((s) => s.values).filter(isFiniteNumber);
  const maxV = Math.max(1, ...allVals);
  const n = labels.length;
  const x = (i: number) => padL + (n === 1 ? innerW / 2 : (innerW * i) / (n - 1));
  // 净投产等可能为负：下界取 min(0, 最小值)，全非负时与原来一致
  const minV = Math.min(0, ...allVals);
  const y = (v: number) => padT + innerH - (innerH * (v - minV)) / (maxV - minV);

  // Y 轴 4 条网格线
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    v: minV + (maxV - minV) * f,
    y: padT + innerH - innerH * f,
  }));

  // X 轴标签抽稀（最多 ~8 个，窄图按宽度再少）
  const maxXLabels = Math.max(2, Math.min(8, Math.floor(innerW / X_LABEL_MIN_SPACE)));
  const step = Math.max(1, Math.ceil(n / maxXLabels));

  return (
    <div ref={wrapRef} style={{ overflowX: "auto" }}>
      <svg width={chartWidth} height={height} role="img" aria-label={ariaLabel}>
        {/* 网格 + Y 轴刻度 */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={padL} y1={t.y} x2={chartWidth - padR} y2={t.y} stroke="#f0f0f0" />
            <text x={padL - 6} y={t.y + 4} textAnchor="end" fontSize="10" fill="#999">
              {formatValue(t.v)}
            </text>
          </g>
        ))}
        {/* X 轴标签 */}
        {labels.map((lb, i) =>
          i % step === 0 ? (
            <text
              key={i}
              x={x(i)}
              y={height - padB + 16}
              textAnchor="middle"
              fontSize="10"
              fill="#999"
            >
              {lb}
            </text>
          ) : null
        )}
        {/* 折线 + 端点 */}
        {cleaned.map((s) => {
          // 连续的非空值连成一段；单独一个点只画点不画线
          const segments: string[][] = [];
          let current: string[] = [];
          s.values.forEach((v, i) => {
            if (v === null) {
              if (current.length) segments.push(current);
              current = [];
            } else {
              current.push(`${x(i)},${y(v)}`);
            }
          });
          if (current.length) segments.push(current);
          return (
            <g key={s.name}>
              {segments
                .filter((seg) => seg.length > 1)
                .map((seg, si) => (
                  <polyline
                    key={si}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    points={seg.join(" ")}
                  />
                ))}
              {s.values.map((v, i) =>
                v === null ? null : (
                  <circle key={i} cx={x(i)} cy={y(v)} r={2.5} fill={s.color} />
                )
              )}
            </g>
          );
        })}
      </svg>
      {/* 图例 */}
      <div style={{ display: "flex", flexWrap: "wrap", columnGap: 16, rowGap: 4, marginTop: 8 }}>
        {series.map((s) => (
          <span key={s.name} style={{ fontSize: 12, color: "#475569" }}>
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                background: s.color,
                borderRadius: 2,
                marginRight: 6,
              }}
            />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
