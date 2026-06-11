import { Card, Descriptions, Tag, Typography } from "antd";
import { useAuthStore } from "@/stores/authStore";

export function HomePage() {
  const user = useAuthStore((s) => s.user);

  return (
    <div>
      <Typography.Title level={3}>欢迎使用服装电商运营管理系统</Typography.Title>
      <Card title="当前用户">
        <Descriptions column={1}>
          <Descriptions.Item label="用户名">{user?.username}</Descriptions.Item>
          <Descriptions.Item label="姓名">
            {user?.display_name || "—"}
          </Descriptions.Item>
          <Descriptions.Item label="邮箱">{user?.email || "—"}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={user?.status === "active" ? "green" : "red"}>
              {user?.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="角色">
            {(user?.roles ?? []).map((r) => (
              <Tag key={r}>{r}</Tag>
            ))}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Typography.Paragraph
        type="secondary"
        style={{ marginTop: 16, fontSize: 13 }}
      >
        商品管理（款式 / 品牌）已上线。设计制版、推广、财务、报表等模块正在陆续接入。
      </Typography.Paragraph>
    </div>
  );
}
