import { Avatar, Dropdown, Layout, Menu, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  AppstoreOutlined,
  DashboardOutlined,
  LogoutOutlined,
  SkinOutlined,
  TagsOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { logout as apiLogout } from "@/features/auth/api";

const { Header, Sider, Content } = Layout;

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  async function handleLogout() {
    try {
      await apiLogout();
    } catch {
      // 忽略 logout 失败
    }
    logout();
    navigate("/login", { replace: true });
  }

  const menuItems: MenuProps["items"] = [
    {
      key: "/",
      icon: <DashboardOutlined />,
      label: <Link to="/">概览</Link>,
    },
    {
      key: "product",
      icon: <AppstoreOutlined />,
      label: "商品管理",
      children: [
        {
          key: "/styles",
          icon: <SkinOutlined />,
          label: <Link to="/styles">款式管理</Link>,
        },
        {
          key: "/brands",
          icon: <TagsOutlined />,
          label: <Link to="/brands">品牌管理</Link>,
        },
      ],
    },
  ];

  const userMenu: MenuProps["items"] = [
    {
      key: "change-password",
      label: "修改密码",
      onClick: () => navigate("/change-password"),
    },
    { type: "divider" },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      danger: true,
      onClick: () => void handleLogout(),
    },
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        breakpoint="lg"
        collapsedWidth="0"
        width={220}
        theme="dark"
      >
        <div
          style={{
            color: "#fff",
            padding: "16px",
            fontSize: 16,
            fontWeight: 600,
          }}
        >
          服装电商管理
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          defaultOpenKeys={["product"]}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: "#fff",
            padding: "0 24px",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <Space style={{ cursor: "pointer" }}>
              <Avatar icon={<UserOutlined />} />
              <Typography.Text>
                {user?.display_name || user?.username || "用户"}
              </Typography.Text>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
