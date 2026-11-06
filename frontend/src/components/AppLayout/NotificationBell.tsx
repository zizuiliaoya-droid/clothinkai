import { useState } from "react";
import { Badge, Button, Empty, List, Popover, Space, Spin, Typography, message } from "antd";
import { BellOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiClient } from "@/services/apiClient";
import { useReadOnlySupport } from "@/stores/supportStore";

interface Notice { id: string; type: string; content: string; link: string | null; is_read: boolean; created_at: string }

export function NotificationBell() {
  const support = useReadOnlySupport();
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const navigate = useNavigate();
  const count = useQuery({ queryKey: ["notifications", "count"],
    queryFn: async () => (await apiClient.get<{count: number}>("/api/notifications/unread-count")).data,
    enabled: !support, refetchInterval: 60_000, retry: false });
  const notices = useQuery({ queryKey: ["notifications", "list"],
    queryFn: async () => (await apiClient.get<Notice[]>("/api/notifications", { params: { limit: 30 } })).data,
    enabled: open && !support, retry: false });
  const go = (link: string) => { setOpen(false); navigate(link); };
  const acknowledge = async (notice: Notice) => {
    try {
      await apiClient.post(`/api/notifications/${notice.id}/read`);
      void client.invalidateQueries({ queryKey: ["notifications"] });
    } catch { message.error("阅读状态暂未同步，请重试"); }
  };
  if (support) return null;
  return <Popover title="通知" trigger="click" placement="bottomRight" open={open} onOpenChange={setOpen}
    content={<div style={{ width: 330, maxHeight: 400, overflowY: "auto" }}>
      {notices.isLoading ? <Spin /> : notices.isError ? <Typography.Text type="secondary">通知暂时无法加载</Typography.Text>
        : <List dataSource={notices.data ?? []} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无通知" /> }}
          renderItem={(notice) => <List.Item><Space direction="vertical" size={4}>
            <Typography.Text strong={!notice.is_read}>{notice.content}</Typography.Text>
            {notice.type === "follow_up_summary" ? <Space>
              <Button type="link" size="small" onClick={() => go("/negotiations?needs_follow_up=true")}>谈款待跟进</Button>
              <Button type="link" size="small" onClick={() => go("/promotions?needs_follow_up=true")}>推广待跟进</Button>
            </Space> : notice.link && <Button type="link" size="small" onClick={() => go(notice.link!)}>查看</Button>}
            {notice.type !== "line_comment" && !notice.is_read && <Button type="link" size="small" onClick={() => void acknowledge(notice)}>标记已读</Button>}
          </Space></List.Item>} />}
    </div>}>
    <Badge count={count.data?.count ?? 0} size="small"><Button aria-label="通知" icon={<BellOutlined />} /></Badge>
  </Popover>;
}
