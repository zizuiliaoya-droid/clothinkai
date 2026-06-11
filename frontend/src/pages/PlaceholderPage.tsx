import { Card, Empty, Typography } from "antd";

interface PlaceholderPageProps {
  title: string;
  /** final.xlsx 对应列（严格对齐），用于占位说明。 */
  columns?: string[];
}

/**
 * 模块占位页：侧边栏导航已就位，业务表格逐模块按 final.xlsx 列填充。
 */
export function PlaceholderPage({ title, columns }: PlaceholderPageProps) {
  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
      }
    >
      <Empty
        description={
          <div>
            <Typography.Paragraph type="secondary">
              「{title}」页面建设中，将严格按 final.xlsx 列结构开发。
            </Typography.Paragraph>
            {columns && columns.length > 0 && (
              <Typography.Paragraph
                type="secondary"
                style={{ fontSize: 12, maxWidth: 720, margin: "0 auto" }}
              >
                列（{columns.length}）：{columns.join(" / ")}
              </Typography.Paragraph>
            )}
          </div>
        }
      />
    </Card>
  );
}
