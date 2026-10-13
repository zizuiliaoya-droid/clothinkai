// 主线抽屉「催发记录」页签（流程线设计 6.6）：现有接口 GET /api/urge/promotions/{id}/records，最新在上。
import { Image, Space, Tag, Timeline, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { listPromotionUrgeRecords, promotionUrgeRecordsKey } from "@/features/urge/api";

export function UrgeRecordsPanel({ promotionId }: { promotionId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: promotionUrgeRecordsKey(promotionId),
    queryFn: () => listPromotionUrgeRecords(promotionId),
  });
  if (isLoading) return <Typography.Text type="secondary">加载中…</Typography.Text>;
  if (error) return <Typography.Text type="secondary">看不到催发记录</Typography.Text>;
  const rows = [...(data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (rows.length === 0) return <Typography.Text type="secondary">这单还没催过</Typography.Text>;
  return (
    <Timeline
      items={rows.map((r) => ({
        children: (
          <Space direction="vertical" size={2}>
            <Space size={6} wrap>
              <Tag color={r.trigger_type === "自动" ? "default" : "orange"}>{r.trigger_type}催发</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(r.created_at).format("YYYY-MM-DD HH:mm")}
                {r.created_by_name ? ` · ${r.created_by_name}` : ""}
              </Typography.Text>
            </Space>
            {r.note && <Typography.Text>{r.note}</Typography.Text>}
            {r.screenshot_url && (
              <Image src={r.screenshot_url} width={96} alt="催发聊天截图" />
            )}
          </Space>
        ),
      }))}
    />
  );
}
