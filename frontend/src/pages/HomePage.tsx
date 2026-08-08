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
      <Card title="已上线能力概览" style={{ marginTop: 16 }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          系统已覆盖商品与设计制版、达人推广、仓库打单、财务结算、运营报表、自动采集及数据质量治理等核心流程。
        </Typography.Paragraph>
        {[
          "商品与设计制版",
          "推广与发文进度",
          "仓库打单与财务",
          "店铺与投产报表",
          "平台凭据与 Worker",
          "导入与数据质量",
        ].map((capability) => (
          <Tag color="blue" key={capability} style={{ marginBottom: 8 }}>
            {capability}
          </Tag>
        ))}
      </Card>
    </div>
  );
}
