import { Empty } from "antd";

export interface Series {
  name: string;
  color: string;
  data: number[];
}

interface Props {
  labels: string[];
  series: Series[];
  height?: number;
  width?: number;
}

/**
 * 轻量 SVG 折线图（无第三方依赖）：多序列折线 + 网格 + 图例 + 端点。
 * 用于单款投产日趋势（支付金额 / 站内花费）等场景。
 */
export function MiniLineChart({ labels, series, height = 260, width = 720 }: Props) {
  if (!labels.length || !series.length) {
    return <Empty description="暂无趋势数据" />;
  }
  const padL = 48;
  const padR = 16;
  const padT = 16;
  const padB = 40;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const allVals = series.flatMap((s) => s.data);
  const maxV = Math.max(1, ...allVals);
  const n = labels.length;
  const x = (i: number) => padL + (n === 1 ? innerW / 2 : (innerW * i) / (n - 1));
  const y = (v: number) => padT + innerH - (innerH * v) / maxV;

  // Y 轴 4 条网格线
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    v: maxV * f,
    y: padT + innerH - innerH * f,
  }));

  // X 轴标签抽稀（最多 ~8 个）
  const step = Math.max(1, Math.ceil(n / 8));

  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={width} height={height} role="img" aria-label="投产趋势折线图">
        {/* 网格 + Y 轴刻度 */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={padL} y1={t.y} x2={width - padR} y2={t.y} stroke="#f0f0f0" />
            <text x={padL - 6} y={t.y + 4} textAnchor="end" fontSize="10" fill="#999">
              {Math.round(t.v).toLocaleString()}
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
        {series.map((s) => {
          const pts = s.data.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={s.name}>
              <polyline
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                points={pts}
              />
              {s.data.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={2.5} fill={s.color} />
              ))}
            </g>
          );
        })}
      </svg>
      {/* 图例 */}
      <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
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
