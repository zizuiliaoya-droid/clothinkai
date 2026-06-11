// 权限守卫组件：按角色或权限决定是否渲染子组件。

import { ReactNode } from "react";
import { useAuthStore } from "@/stores/authStore";

interface PermissionGateProps {
  children: ReactNode;
  /** 要求的角色 code（任一满足即可）。空数组表示不限制。 */
  requireAnyRole?: string[];
  /** 备选渲染（无权限时显示）。 */
  fallback?: ReactNode;
}

export function PermissionGate({
  children,
  requireAnyRole = [],
  fallback = null,
}: PermissionGateProps) {
  const user = useAuthStore((s) => s.user);

  if (!user) return <>{fallback}</>;

  if (requireAnyRole.length === 0) {
    return <>{children}</>;
  }

  const hasRole = requireAnyRole.some((role) => user.roles.includes(role));
  return hasRole ? <>{children}</> : <>{fallback}</>;
}
