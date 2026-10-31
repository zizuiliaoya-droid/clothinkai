"""RBAC 权限校验 + 装饰器 + Pydantic 动态 Schema 框架。

业务规则（BR-PERM-001）：
    effective(U) = (roles_perms ∪ grants) - revokes
    优先级：撤销 > 授予 > 角色

U01 阶段：实现 require_permission 装饰器 + check_permission + Redis 缓存。
U09 阶段：在此文件追加 build_schema_for_user 动态 Schema 实现。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Set
from dataclasses import dataclass
from typing import Any

from fastapi import Depends

from app.core.cache import cache
from app.core.config import settings
from app.core.exceptions import PermissionDeniedError, TokenInvalidError

PERM_CACHE_PREFIX = "perm:user:v2:"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


def scope_matches(scopes: Set[str], scope: str, action: str) -> bool:
    """授权与拒绝使用同一匹配规则，保留现有一级域通配语义。"""
    # 部分旧端点传完整 scope 常量并另传 action，统一为同一能力后再匹配。
    scope = scope.removesuffix(f":{action}")
    module = scope.split(":", 1)[0].split(".", 1)[0]
    return bool({"*", f"{scope}:{action}", f"{module}.*:*", f"{module}.*:{action}"} & scopes)


@dataclass(frozen=True)
class EffectivePermissions:
    """授权候选与个人拒绝；判定必须走 has()，拒绝优先于任何通配授权。"""

    user_id: str
    scopes: frozenset[str]
    revokes: frozenset[str] = frozenset()

    def has(self, scope: str, action: str = "read") -> bool:
        return not scope_matches(self.revokes, scope, action) and scope_matches(
            self.scopes, scope, action
        )

    def has_explicit(self, scope: str, action: str) -> bool:
        return f"{scope}:{action}" in self.scopes and self.has(scope, action)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


async def _load_from_cache(user_id: str) -> EffectivePermissions | None:
    raw = await cache.get(f"{PERM_CACHE_PREFIX}{user_id}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return EffectivePermissions(
            user_id=user_id, scopes=frozenset(data["scopes"]), revokes=frozenset(data["revokes"])
        )
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


async def _save_to_cache(perms: EffectivePermissions) -> None:
    await cache.setex(
        f"{PERM_CACHE_PREFIX}{perms.user_id}",
        settings.PERM_CACHE_TTL_SECONDS,
        json.dumps({"scopes": sorted(perms.scopes), "revokes": sorted(perms.revokes)}),
    )


async def invalidate_user_permissions_cache(user_id: str) -> None:
    """权限矩阵变更时调用，立即清除缓存。"""
    await cache.delete(f"{PERM_CACHE_PREFIX}{user_id}")


# ---------------------------------------------------------------------------
# 校验入口
# ---------------------------------------------------------------------------


def check_permissions(perms: EffectivePermissions, scope: str, action: str = "read") -> bool:
    return perms.has(scope, action)


# ---------------------------------------------------------------------------
# FastAPI Depends 装饰器（粗粒度）
# ---------------------------------------------------------------------------


def require_permission(scope: str, action: str = "read") -> Callable[..., Any]:
    """生成 FastAPI Depends：校验当前用户拥有 (scope, action) 权限。

    使用方式：
        @router.get("/x", dependencies=[Depends(require_permission("auth.user", "read"))])
    """

    async def _checker(
        # 通过依赖注入加载当前用户（在 modules/auth/api.py 实现）
        # 此处不直接 import 避免循环依赖，由调用方在 router 上传入
        current_perms: EffectivePermissions = Depends(_get_current_perms_placeholder),
    ) -> None:
        if not current_perms.has(scope, action):
            raise PermissionDeniedError(
                f"缺少权限 {scope}:{action}",
                details={"required_scope": scope, "required_action": action},
            )

    return _checker


# ---------------------------------------------------------------------------
# 占位：当前用户权限的依赖
# ---------------------------------------------------------------------------
# modules/auth/api.py 会用真实实现覆盖此函数（通过 app.dependency_overrides 或
# 直接在 router 中引用 modules.auth.deps.get_current_perms）。
# ---------------------------------------------------------------------------


async def _get_current_perms_placeholder() -> EffectivePermissions:
    """占位实现，实际由 modules/auth/deps.py 的 get_current_perms 替换。"""
    raise TokenInvalidError("权限依赖未注册（需 modules/auth/deps.py 配置）")


# ---------------------------------------------------------------------------
# U09 字段级权限占位（实现留 U09）
# ---------------------------------------------------------------------------


def build_schema_for_user(base_cls: type, _user: Any) -> type:
    """根据用户字段权限动态裁剪响应 Schema。

    U01 阶段不实施字段级权限（按执行计划质量门），返回原 Schema。
    U09 启用时此函数会扫描 base_cls 的字段元数据，按权限移除字段。
    """
    return base_cls
