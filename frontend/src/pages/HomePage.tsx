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
        当前为 U01 单元（认证 + 多租户基础）。后续业务模块（设计制版、推广、财务、报表等）将在
        V1 / V2 / P3 阶段陆续上线。
      </Typography.Paragraph>
    </div>
  );
}
