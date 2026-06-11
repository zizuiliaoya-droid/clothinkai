import { Avatar, Dropdown, Layout, Menu, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  AppstoreOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DollarOutlined,
  HighlightOutlined,
  LogoutOutlined,
  NotificationOutlined,
  SettingOutlined,
  SkinOutlined,
  TagsOutlined,
  TeamOutlined,
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
      key: "design",
      icon: <HighlightOutlined />,
      label: "设计制版",
      children: [
        { key: "/designs", label: <Link to="/designs">设计管理</Link> },
        { key: "/patterns", label: <Link to="/patterns">制版管理</Link> },
        { key: "/crafts", label: <Link to="/crafts">工艺管理</Link> },
        { key: "/pricing", label: <Link to="/pricing">核价管理</Link> },
      ],
    },
    {
      key: "data",
      icon: <AppstoreOutlined />,
      label: "数据管理",
      children: [
        {
          key: "/skus",
          icon: <AppstoreOutlined />,
          label: <Link to="/skus">商品成本表</Link>,
        },
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
        {
          key: "/bloggers",
          icon: <TeamOutlined />,
          label: <Link to="/bloggers">博主库</Link>,
        },
        { key: "/qianniu", label: <Link to="/qianniu">千牛数据</Link> },
        { key: "/ad-data", label: <Link to="/ad-data">单品站内推广</Link> },
      ],
    },
    {
      key: "promotion",
      icon: <NotificationOutlined />,
      label: "推广管理",
      children: [
        { key: "/promotions", label: <Link to="/promotions">站外推广</Link> },
        {
          key: "/work-progress",
          label: <Link to="/work-progress">工作进度表</Link>,
        },
        {
          key: "/publish-target",
          label: <Link to="/publish-target">爆款约篇数量</Link>,
        },
        {
          key: "/publish-progress",
          label: <Link to="/publish-progress">发文进度表</Link>,
        },
      ],
    },
    {
      key: "finance",
      icon: <DollarOutlined />,
      label: "财务管理",
      children: [
        { key: "/settlements", label: <Link to="/settlements">财务结款</Link> },
        { key: "/tao-orders", label: <Link to="/tao-orders">拍单</Link> },
        { key: "/brush-orders", label: <Link to="/brush-orders">刷单</Link> },
        { key: "/balance", label: <Link to="/balance">余额核对</Link> },
      ],
    },
    {
      key: "report",
      icon: <BarChartOutlined />,
      label: "报表与分析",
      children: [
        { key: "/store-daily", label: <Link to="/store-daily">店铺数据</Link> },
        { key: "/production", label: <Link to="/production">投产报表</Link> },
        { key: "/bi", label: <Link to="/bi">BI看板</Link> },
      ],
    },
    {
      key: "system",
      icon: <SettingOutlined />,
      label: "系统管理",
      children: [
        { key: "/users", label: <Link to="/users">用户管理</Link> },
        { key: "/imports", label: <Link to="/imports">数据导入</Link> },
        { key: "/settings", label: <Link to="/settings">系统设置</Link> },
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
          defaultOpenKeys={["design", "data", "promotion", "finance", "report", "system"]}
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
