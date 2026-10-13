import { Button, Empty } from "antd";
import { useState, type ReactNode } from "react";

export interface ChartTableRow {
  key: string;
  cells: ReactNode[];
}

interface Props {
  /** 图表标题：用作表格 caption 与切换按钮的说明。 */
  title: string;
  /** 「查看数据」表格的列头。 */
  columns: string[];
  rows: ChartTableRow[];
  /** 为 true 时只显示 emptyText，不出切换按钮。 */
  empty: boolean;
  emptyText: string;
  children: ReactNode;
}

const cellStyle = {
  padding: "6px 12px",
  borderBottom: "1px solid #e2e8f0",
  textAlign: "left" as const,
  whiteSpace: "nowrap" as const,
};

/**
 * 图表外框：右上角「查看数据」切换（原生 button + aria-pressed，键盘 Enter / 空格可切），
 * 打开后用带 caption 的表格替代图形，给键盘与读屏用户读数、下钻。
 */
export function ChartFrame({ title, columns, rows, empty, emptyText, children }: Props) {
  const [showTable, setShowTable] = useState(false);
  if (empty) {
    return <Empty description={emptyText} />;
  }
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button
          type="link"
          size="small"
          htmlType="button"
          aria-pressed={showTable}
          onClick={() => setShowTable((v) => !v)}
        >
          查看数据
        </Button>
      </div>
      {showTable ? (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13, color: "#0f172a" }}>
            <caption style={{ captionSide: "top", textAlign: "left", padding: "4px 0", color: "#475569" }}>
              {title}
            </caption>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c} scope="col" style={{ ...cellStyle, background: "#f8fafc", fontWeight: 600 }}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  {r.cells.map((cell, i) => (
                    <td key={i} style={cellStyle}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        children
      )}
    </div>
  );
}
