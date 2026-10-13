import { Navigate, useLocation } from "react-router-dom";

/** 旧路由 /store-daily → /store/daily（7e R4）：查询串原样带过去，旧书签与别人发来的链接不丢筛选。 */
export function LegacyStoreRedirect() {
  const location = useLocation();
  return <Navigate to={{ pathname: "/store/daily", search: location.search }} replace />;
}
