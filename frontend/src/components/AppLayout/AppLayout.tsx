import { Avatar, Dropdown, Layout, Menu, Space, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  AppstoreOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DollarOutlined,
  GiftOutlined,
  HighlightOutlined,
  KeyOutlined,
  LinkOutlined,
  LogoutOutlined,
  NotificationOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
  UserOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { logout as apiLogout } from "@/features/auth/api";
import { useReportCapabilities } from "@/features/report/useReportCapabilities";
import { ModuleNavigation } from "./ModuleNavigation";

const { Header, Sider, Content } = Layout;

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const roles = user?.roles ?? [];
  const isSystemAdmin = roles.some(
    (role) => role === "admin" || role === "platform_admin"
  );
  const canViewCredentials =
    isSystemAdmin || roles.includes("operations");
  const canViewDataQuality = canViewCredentials;
  // 平台链接是运维视图：绑错一条链接，整条销售数据就记到别的商品名下
  const canViewPlatformLinks = canViewCredentials;
  // 仓库发货只走 promotion_ship:fill / export（060 起只授 warehouse，管理员靠 *）；
  // PR / 主管 / 财务 / 运营点进去是 403，菜单里就不放。只管显示，后端 403 才是闸门
  const canViewWarehouse = isSystemAdmin || roles.includes("warehouse");
  // 报表菜单按 capabilities（/me 只给角色，算不了 scope）：能力没回来前不显示，免得先出现再收回。
  // 「BI看板」看全部（full）与只看本人（personal，7e R6 起 PR 进去是个人版）都显示。7e R4 起发文进度表、
  // 单品投产、店铺日 / 周 / 月也按能力显隐（财务没有这几张表的读权限，菜单里就不出现）。只管显示，后端 403 才是闸门
  const { data: reportCaps } = useReportCapabilities();
  const canViewWorkProgress = reportCaps?.work_progress != null;
  const canViewPublishProgress = reportCaps?.publish_progress != null;
  const canViewBi = reportCaps?.bi != null;
  const canViewProduction = reportCaps?.production ?? false;
  const canViewStore = reportCaps?.store_read ?? false;
  // 「投产分析」分组（7e R4，设计 2.4）：单品投产 + 店铺日 / 周 / 月；空组不出
  const roiItems = [
    ...(canViewProduction
      ? [{ key: "/production", label: <Link to="/production">单品投产</Link> }]
      : []),
    ...(canViewStore
      ? [
          { key: "/store/daily", label: <Link to="/store/daily">店铺日明细</Link> },
          { key: "/store/weekly", label: <Link to="/store/weekly">店铺周汇总</Link> },
          { key: "/store/monthly", label: <Link to="/store/monthly">店铺月度汇总</Link> },
        ]
      : []),
  ];
  const reportItems: NonNullable<MenuProps["items"]> = [
    ...(roiItems.length
      ? [{ key: "roi", type: "group" as const, label: "投产分析", children: roiItems }]
      : []),
    ...(canViewBi ? [{ key: "/bi", label: <Link to="/bi">BI看板</Link> }] : []),
  ];

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
          key: "/goods",
          icon: <GiftOutlined />,
          label: <Link to="/goods">商品 / 套装</Link>,
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
        // 谈款在推广单之前：审核通过才会生成站外推广单，菜单顺序跟着流程走
        { key: "/negotiations", label: <Link to="/negotiations">谈款审核</Link> },
        { key: "/promotions", label: <Link to="/promotions">站外推广</Link> },
        { key: "/urge-tasks", label: <Link to="/urge-tasks">催发任务</Link> },
        ...(canViewWarehouse
          ? [{ key: "/warehouse-orders", label: <Link to="/warehouse-orders">仓库发货</Link> }]
          : []),
        ...(canViewWorkProgress
          ? [
              {
                key: "/work-progress",
                label: <Link to="/work-progress">工作进度表</Link>,
              },
            ]
          : []),
        {
          key: "/publish-target",
          label: <Link to="/publish-target">爆款约篇数量</Link>,
        },
        ...(canViewPublishProgress
          ? [
              {
                key: "/publish-progress",
                label: <Link to="/publish-progress">发文进度表</Link>,
              },
            ]
          : []),
      ],
    },
    {
      key: "finance",
      icon: <DollarOutlined />,
      label: "财务管理",
      children: [
        { key: "/settlements", label: <Link to="/settlements">财务结款</Link> },
        {
          key: "/order-adjustments",
          label: <Link to="/order-adjustments">拍单 / 刷单</Link>,
        },
        { key: "/balance", label: <Link to="/balance">余额核对</Link> },
      ],
    },
    // 整组没有可看的报表（含能力没回来前）就不出「报表与分析」
    ...(reportItems.length
      ? [
          {
            key: "report",
            icon: <BarChartOutlined />,
            label: "报表与分析",
            children: reportItems,
          },
        ]
      : []),
    {
      key: "system",
      icon: <SettingOutlined />,
      label: "系统管理",
      children: [
        { key: "/users", label: <Link to="/users">用户管理</Link> },
        { key: "/imports", label: <Link to="/imports">导入记录</Link> },
        ...(canViewCredentials
          ? [
              {
                key: "/credentials",
                icon: <SafetyCertificateOutlined />,
                label: <Link to="/credentials">平台凭据</Link>,
              },
            ]
          : []),
        ...(isSystemAdmin
          ? [
              {
                key: "/worker-tokens",
                icon: <KeyOutlined />,
                label: <Link to="/worker-tokens">Worker Token</Link>,
              },
            ]
          : []),
        ...(canViewDataQuality
          ? [
              {
                key: "/data-quality",
                icon: <WarningOutlined />,
                label: <Link to="/data-quality">数据质量</Link>,
              },
            ]
          : []),
        ...(canViewPlatformLinks
          ? [
              {
                key: "/platform-links",
                icon: <LinkOutlined />,
                label: <Link to="/platform-links">平台链接</Link>,
              },
            ]
          : []),
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

  // 仓库角色只能操作发货：仅保留「仓库发货」菜单（admin/平台管理员不受限）
  const isWarehouseOnly =
    roles.includes("warehouse") &&
    !roles.some((r) => r === "admin" || r === "platform_admin");
  const visibleMenuItems: MenuProps["items"] = isWarehouseOnly
    ? [
        {
          key: "/warehouse-orders",
          icon: <NotificationOutlined />,
          label: <Link to="/warehouse-orders">仓库发货</Link>,
        },
      ]
    : menuItems;

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
          items={visibleMenuItems}
        />
      </Sider>
      <Layout className="app-workspace">
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
        <ModuleNavigation productionAccess={reportCaps?.production} />
        <Content className="app-content">
          <Outlet key={`${user?.id ?? ""}:${[...roles].sort().join(",")}`} />
        </Content>
      </Layout>
    </Layout>
  );
}
