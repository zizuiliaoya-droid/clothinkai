import { ConfigProvider, Spin } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { queryClient } from "@/services/queryClient";
import { AppLayout } from "@/components/AppLayout/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { HomePage } from "@/pages/HomePage";
import { ChangePasswordPage } from "@/pages/ChangePasswordPage";
import { BrandListPage } from "@/pages/BrandListPage";
import { StyleListPage } from "@/pages/StyleListPage";
import { BloggerListPage } from "@/pages/BloggerListPage";
import { PromotionListPage } from "@/pages/PromotionListPage";
import { CostTablePage } from "@/pages/CostTablePage";
import { SettlementListPage } from "@/pages/SettlementListPage";
import { OrderAdjustmentPage } from "@/pages/OrderAdjustmentPage";
import { BalancePage } from "@/pages/BalancePage";
import { WorkProgressPage } from "@/pages/WorkProgressPage";
import { PublishTargetPage } from "@/pages/PublishTargetPage";
import { PublishProgressPage } from "@/pages/PublishProgressPage";
import { StoreDailyPage } from "@/pages/StoreDailyPage";
import { ProductionPage } from "@/pages/ProductionPage";
import { DesignListPage } from "@/pages/DesignListPage";
import { UserListPage } from "@/pages/UserListPage";
import { ImportListPage } from "@/pages/ImportListPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { BiDashboardPage } from "@/pages/BiDashboardPage";
import { DailyDataPage } from "@/pages/DailyDataPage";
import { listQianniu, listAdDaily } from "@/features/collect/api";
import type { QianniuRow, AdRow } from "@/features/collect/api";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { getMe } from "@/features/auth/api";

// 受保护路由：未登录跳 /login；must_change 跳 /change-password
function ProtectedRoute({ children }: { children: JSX.Element }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const mustChange = useAuthStore((s) => s.mustChangePassword);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  if (mustChange && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }
  return children;
}

function AppRoutes() {
  const [bootstrapping, setBootstrapping] = useState(true);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setUser = useAuthStore((s) => s.setUser);
  const logout = useAuthStore((s) => s.logout);

  // 启动时如果有 token 就拉一次 /me 验证
  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      if (!isAuthenticated) {
        setBootstrapping(false);
        return;
      }
      try {
        const user = await getMe();
        if (!cancelled) setUser(user);
      } catch {
        if (!cancelled) logout();
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
    }
    void bootstrap();

    // 监听 401 全局事件 → 登出
    const onUnauth = () => logout();
    window.addEventListener("auth:unauthorized", onUnauth);
    return () => {
      cancelled = true;
      window.removeEventListener("auth:unauthorized", onUnauth);
    };
  }, [isAuthenticated, setUser, logout]);

  if (bootstrapping) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/change-password" element={<ChangePasswordPage />} />
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/styles" element={<StyleListPage />} />
        <Route path="/brands" element={<BrandListPage />} />
        <Route path="/bloggers" element={<BloggerListPage />} />
        <Route path="/promotions" element={<PromotionListPage />} />
        {/* 设计制版 */}
        <Route path="/designs" element={<DesignListPage title="设计管理" />} />
        <Route path="/patterns" element={<DesignListPage title="制版管理" statuses={["制版中"]} />} />
        <Route path="/crafts" element={<DesignListPage title="工艺管理" statuses={["工艺录入"]} />} />
        <Route path="/pricing" element={<DesignListPage title="核价管理" statuses={["待补全", "待核价"]} />} />
        {/* 数据管理 */}
        <Route
          path="/skus"
          element={<CostTablePage />}
        />
        <Route path="/qianniu" element={
          <DailyDataPage<QianniuRow>
            title="千牛数据"
            queryKey="qianniu"
            fetchFn={listQianniu}
            totalCols={38}
            typedColumns={[
              { title: "统计日期", dataIndex: "date", width: 110, fixed: "left" },
              { title: "商品ID", dataIndex: "platform_id", width: 130 },
              { title: "商品访客数", dataIndex: "visitors", width: 100 },
              { title: "支付金额", dataIndex: "pay_amount", width: 110, render: (v) => (v == null ? "—" : `¥${v}`) },
              { title: "支付件数", dataIndex: "pay_orders", width: 100 },
            ]}
          />
        } />
        <Route path="/ad-data" element={
          <DailyDataPage<AdRow>
            title="单品站内推广数据"
            queryKey="ad-daily"
            fetchFn={listAdDaily}
            totalCols={72}
            typedColumns={[
              { title: "日期", dataIndex: "date", width: 110, fixed: "left" },
              { title: "主体ID", dataIndex: "platform_id", width: 130 },
              { title: "花费", dataIndex: "cost", width: 110, render: (v) => (v == null ? "—" : `¥${v}`) },
              { title: "展现量", dataIndex: "impressions", width: 100 },
              { title: "点击量", dataIndex: "clicks", width: 100 },
              { title: "总成交金额", dataIndex: "gmv", width: 120, render: (v) => (v == null ? "—" : `¥${v}`) },
            ]}
          />
        } />
        {/* 推广管理 */}
        <Route path="/work-progress" element={<WorkProgressPage />} />
        <Route path="/publish-target" element={<PublishTargetPage />} />
        <Route path="/publish-progress" element={<PublishProgressPage />} />
        {/* 财务管理 */}
        <Route path="/settlements" element={<SettlementListPage />} />
        <Route path="/tao-orders" element={<OrderAdjustmentPage orderType="拍单" />} />
        <Route path="/brush-orders" element={<OrderAdjustmentPage orderType="刷单" />} />
        <Route path="/balance" element={<BalancePage />} />
        {/* 报表与分析 */}
        <Route path="/store-daily" element={<StoreDailyPage />} />
        <Route path="/production" element={<ProductionPage />} />
        <Route path="/bi" element={<BiDashboardPage />} />
        {/* 系统管理 */}
        <Route path="/users" element={<UserListPage />} />
        <Route path="/imports" element={<ImportListPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
