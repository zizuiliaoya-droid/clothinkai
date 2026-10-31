import { Alert, Button, Layout, Space, Typography } from "antd";
import { Outlet, useNavigate } from "react-router-dom";
import { logout as apiLogout } from "@/features/auth/api";
import { useAuthStore } from "@/stores/authStore";
import { useSupportStore } from "@/stores/supportStore";

export function PlatformLayout() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const notice = useSupportStore((state) => state.notice);
  const dismiss = useSupportStore((state) => state.dismissNotice);
  async function leave() {
    try { await apiLogout(); } catch { /* 本地退出仍生效。 */ }
    logout();
    navigate("/login", { replace: true });
  }
  return <Layout style={{ minHeight: "100vh" }}>
    <Layout.Header style={{ background: "#fff", padding: "0 24px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
      <Typography.Title level={4} style={{ margin: 0 }}>ClothinkAI · 平台管理</Typography.Title>
      <Space><Typography.Text>{user?.display_name || user?.username}</Typography.Text>
        <Button onClick={() => navigate("/change-password")}>修改自己的密码</Button>
        <Button onClick={() => void leave()}>退出登录</Button>
      </Space>
    </Layout.Header>
    <Layout.Content style={{ padding: 24, width: "100%", maxWidth: 1440, margin: "0 auto" }}>
      {notice && <Alert showIcon type="info" message={notice} closable onClose={dismiss} style={{ marginBottom: 16 }} />}
      <Typography.Paragraph type="secondary">管理客户开户与启停。排查客户问题时进入限时只读视图，客户业务由客户自己处理。</Typography.Paragraph>
      <Outlet />
    </Layout.Content>
  </Layout>;
}
