"""平台链接的权限 scope 约束（不碰数据库，纯规则校验）。

平台链接（千牛商品ID / 万相台主体ID 与商品、渠道的绑定）是运维视图：绑错一条链接，
整条销售数据就记到别的商品名下。所以它必须真正挡住业务角色 —— 这组测试把两个
容易踩的坑钉住。
"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.product import platform_product_api


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id="u", scopes=frozenset(scopes))


@pytest.mark.unit
class TestPlatformLinkScope:
    def test_endpoints_declare_ops_scope(self) -> None:
        """不能挂回 product.* 之下 —— 那样等于没设门槛。"""
        assert platform_product_api.SCOPE == "ops.platform_link"
        assert not platform_product_api.SCOPE.startswith("product")

    def test_product_wildcards_do_not_grant_platform_link(self) -> None:
        """坑一：has() 的前缀通配只看 scope 第一段。

        原来声明 product.platform 时，跟单的 product.*:* 与运营/设计的 product.*:read
        会直接命中，业务角色照样能改链接。
        """
        for perms in (_perms("product.*:*"), _perms("product.*:read")):
            assert not perms.has("ops.platform_link", "read")
            assert not perms.has("ops.platform_link", "write")

    def test_sub_scope_wildcard_is_not_a_valid_grant(self) -> None:
        """坑二：``ops.platform_link:*`` 授了等于没授。

        has() 的通配形式只支持「第一段.*:action」，子 scope 通配匹配不上，
        所以角色矩阵必须列出两条具体权限。
        """
        assert not _perms("ops.platform_link:*").has("ops.platform_link", "read")

    def test_explicit_grant_and_admin_wildcard_pass(self) -> None:
        ops = _perms("ops.platform_link:read", "ops.platform_link:write")
        assert ops.has("ops.platform_link", "read")
        assert ops.has("ops.platform_link", "write")
        assert _perms("*").has("ops.platform_link", "write")


@pytest.mark.unit
class TestDefaultRoleMatrix:
    def test_operations_gets_platform_link(self) -> None:
        """运营要能维护平台链接 —— 这是它的职责。"""
        ops_role = next(r for r in DEFAULT_ROLES if r.code == "operations")
        assert "ops.platform_link:read" in ops_role.permissions
        assert "ops.platform_link:write" in ops_role.permissions

    def test_business_roles_do_not_get_platform_link(self) -> None:
        """跟单、设计、PR、财务、仓库都不该碰平台链接。"""
        for role in DEFAULT_ROLES:
            if role.code in {"admin", "platform_admin", "operations"}:
                continue
            offenders = [p for p in role.permissions if p.startswith("ops.platform_link")]
            assert not offenders, f"{role.code} 不该有平台链接权限：{offenders}"
