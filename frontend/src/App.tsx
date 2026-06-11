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
        <Route path="/designs" element={<PlaceholderPage title="设计管理" />} />
        <Route path="/patterns" element={<PlaceholderPage title="制版管理" />} />
        <Route path="/crafts" element={<PlaceholderPage title="工艺管理" />} />
        <Route path="/pricing" element={<PlaceholderPage title="核价管理" />} />
        {/* 数据管理 */}
        <Route
          path="/skus"
          element={<CostTablePage />}
        />
        <Route path="/qianniu" element={<PlaceholderPage title="千牛数据" />} />
        <Route path="/ad-data" element={<PlaceholderPage title="单品站内推广数据" />} />
        {/* 推广管理 */}
        <Route path="/work-progress" element={<PlaceholderPage title="工作进度表" columns={["负责PR", "约篇件数", "档期内", "催发", "重要催发", "超时", "已发布", "信息完整度", "已取消", "应召回", "召回成功", "召回完成率", "超时率", "月度完成率", "爆文数", "爆文率", "点赞数", "成本(含衣服)", "CPL(元/赞)"]} />} />
        <Route path="/publish-target" element={<PlaceholderPage title="爆款约篇数量" />} />
        <Route path="/publish-progress" element={<PlaceholderPage title="发文进度表" />} />
        {/* 财务管理 */}
        <Route path="/settlements" element={<PlaceholderPage title="财务结款" columns={["月份", "日期", "大类", "项目", "款式编码", "款式", "金额", "寄/送", "博主名", "付款金额", "付款日期", "衣服成本", "总成本", "付款图片", "备注"]} />} />
        <Route path="/tao-orders" element={<PlaceholderPage title="拍单" columns={["销售类型", "拍单日期", "订单号", "博主ID/微信ID", "款式", "款号", "金额", "付款金额", "付款日期"]} />} />
        <Route path="/brush-orders" element={<PlaceholderPage title="刷单" />} />
        <Route path="/balance" element={<PlaceholderPage title="余额核对" columns={["日期", "充值收入", "推广支出", "刷/拍单支出", "余额", "余额截图", "备注"]} />} />
        {/* 报表与分析 */}
        <Route path="/store-daily" element={<PlaceholderPage title="店铺数据" />} />
        <Route path="/production" element={<PlaceholderPage title="投产报表" />} />
        <Route path="/bi" element={<PlaceholderPage title="BI看板" />} />
        {/* 系统管理 */}
        <Route path="/users" element={<PlaceholderPage title="用户管理" />} />
        <Route path="/imports" element={<PlaceholderPage title="数据导入" />} />
        <Route path="/settings" element={<PlaceholderPage title="系统设置" />} />
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
