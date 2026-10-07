"""8a-7：导入的来源级权限矩阵（importer/access.py，设计 §4.6）。

用 ``DEFAULT_ROLES`` 每个角色的 permissions 构造 ``EffectivePermissions``，逐角色核对：
商品资料（manual_style_sku）只认 ``product.import:write``——跟单、运营、管理员能上传 / 改映射 /
裁决，PR / 主管收回；博主的裁决看 ``blogger:write``；其余来源与 8a 之前一致。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.importer import access

STYLE_SKU = "manual_style_sku"
BLOGGER = "manual_blogger"

ALL_ROLES = (
    "admin",
    "platform_admin",
    "designer",
    "design_assistant",
    "pattern_maker",
    "merchandiser",
    "pr",
    "pr_manager",
    "finance",
    "operations",
    "warehouse",
)


def _role_perms(code: str, *extra: str) -> EffectivePermissions:
    role = next(r for r in DEFAULT_ROLES if r.code == code)
    return EffectivePermissions(user_id=code, scopes=frozenset((*role.permissions, *extra)))


def _who(predicate: Any) -> set[str]:
    return {code for code in ALL_ROLES if predicate(_role_perms(code))}


def _can_upload(source: str) -> Any:
    def check(perms: EffectivePermissions) -> bool:
        try:
            access.require_write(perms, source)
        except PermissionDeniedError:
            return False
        return True

    return check


def _can_map(source: str) -> Any:
    def check(perms: EffectivePermissions) -> bool:
        try:
            access.require_mapping(perms, source)
        except PermissionDeniedError:
            return False
        return True

    return check


def _can_resolve(source: str) -> Any:
    def check(perms: EffectivePermissions) -> bool:
        try:
            access.require_resolve(perms, source)
        except PermissionDeniedError:
            return False
        return True

    return check


@pytest.mark.unit
class TestStyleSkuMatrix:
    """设计 §4.6 矩阵的商品资料四行。"""

    def test_upload_and_retry(self) -> None:
        assert _who(_can_upload(STYLE_SKU)) == {
            "admin",
            "platform_admin",
            "merchandiser",
            "operations",
        }

    def test_mapping(self) -> None:
        assert _who(_can_map(STYLE_SKU)) == {
            "admin",
            "platform_admin",
            "merchandiser",
            "operations",
        }

    def test_view(self) -> None:
        # PR / 主管凭 importer.batch:read 仍能看（成本价、采购价脱敏）
        assert _who(lambda p: access.can_view(p, STYLE_SKU)) == {
            "admin",
            "platform_admin",
            "merchandiser",
            "operations",
            "pr",
            "pr_manager",
        }

    def test_resolve(self) -> None:
        assert _who(_can_resolve(STYLE_SKU)) == {
            "admin",
            "platform_admin",
            "merchandiser",
            "operations",
        }

    def test_pr_keeps_importer_scopes_but_loses_style_sku(self) -> None:
        """PR / 主管的角色数据没改：靠「商品资料只认 product.import:write」收回。"""
        for code in ("pr", "pr_manager"):
            perms = _role_perms(code)
            assert perms.has("importer.batch", "write")
            with pytest.raises(PermissionDeniedError) as exc:
                access.require_write(perms, STYLE_SKU)
            assert exc.value.details == {
                "required_scope": "product.import",
                "required_action": "write",
                "source": STYLE_SKU,
            }

    def test_w1_product_style_read_brings_no_import(self) -> None:
        """W1 给 PR / 主管的精确 product.style:read 不带出任何商品资料导入能力。"""
        for code in ("pr", "pr_manager"):
            perms = _role_perms(code, "product.style:read")
            assert not _can_upload(STYLE_SKU)(perms)
            assert not _can_map(STYLE_SKU)(perms)
            assert not _can_resolve(STYLE_SKU)(perms)
            assert not perms.has("product.import", "write")

    def test_designer_product_read_does_not_hit_import_write(self) -> None:
        for code in ("designer", "design_assistant"):
            perms = _role_perms(code)
            assert perms.has("product.import", "read")  # product.*:read 的通配
            assert not perms.has("product.import", "write")
            assert not access.can_view(perms, STYLE_SKU)


@pytest.mark.unit
class TestBloggerMatrix:
    def test_view(self) -> None:
        assert _who(lambda p: access.can_view(p, BLOGGER)) == {
            "admin",
            "platform_admin",
            "operations",
            "pr",
            "pr_manager",
        }

    def test_resolve(self) -> None:
        assert _who(_can_resolve(BLOGGER)) == {"admin", "platform_admin", "pr", "pr_manager"}

    def test_upload_unchanged(self) -> None:
        assert _who(_can_upload(BLOGGER)) == {"admin", "platform_admin", "pr", "pr_manager"}

    def test_mapping_unchanged(self) -> None:
        assert _who(_can_map(BLOGGER)) == {"admin", "platform_admin", "pr_manager"}


@pytest.mark.unit
class TestOtherSources:
    @pytest.mark.parametrize(
        "source",
        [
            "manual_promotion",
            "manual_settlement",
            "qianniu",
            "wanxiangtai",
            "manual_tao_order",
            "manual_brush_order",
            "huitun",
            "fake_source",
        ],
    )
    def test_default_rules(self, source: str) -> None:
        assert access.access_for(source) == access._DEFAULT
        assert _who(_can_upload(source)) == {"admin", "platform_admin", "pr", "pr_manager"}
        assert _who(_can_map(source)) == {"admin", "platform_admin", "pr_manager"}
        assert _who(lambda p: access.can_view(p, source)) == {
            "admin",
            "platform_admin",
            "operations",
            "pr",
            "pr_manager",
        }


@pytest.mark.unit
class TestVisibleSources:
    @pytest.mark.parametrize("code", ["admin", "platform_admin", "operations", "pr", "pr_manager"])
    def test_all_sources(self, code: str) -> None:
        assert access.visible_sources(_role_perms(code)) is None

    def test_merchandiser_only_style_sku(self) -> None:
        assert access.visible_sources(_role_perms("merchandiser")) == frozenset({STYLE_SKU})

    @pytest.mark.parametrize(
        "code", ["finance", "pattern_maker", "warehouse", "designer", "design_assistant"]
    )
    def test_none_visible(self, code: str) -> None:
        assert access.visible_sources(_role_perms(code)) == frozenset()


@pytest.mark.unit
class TestRequireView:
    def test_message_names_required_scope(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc:
            access.require_view(_role_perms("merchandiser"), "qianniu")
        assert exc.value.details["required_scope"] == "importer.batch"
        assert exc.value.details["source"] == "qianniu"

    def test_passes_when_visible(self) -> None:
        access.require_view(_role_perms("merchandiser"), STYLE_SKU)
        access.require_view(_role_perms("pr"), "qianniu")


@pytest.mark.unit
class TestDescribeAccess:
    @pytest.fixture
    def registered(self) -> Iterator[None]:
        from app.main import register_import_adapters
        from app.modules.importer.registry import ImportAdapterRegistry

        saved = dict(ImportAdapterRegistry._adapters)
        register_import_adapters()
        try:
            yield
        finally:
            ImportAdapterRegistry._adapters.clear()
            ImportAdapterRegistry._adapters.update(saved)

    def test_every_registered_source_listed_with_label(self, registered: None) -> None:
        from app.modules.importer.registry import ImportAdapterRegistry

        items = access.describe_access(_role_perms("operations"))
        assert [i["source"] for i in items] == sorted(ImportAdapterRegistry.sources())
        for item in items:
            assert item["label"] == access.SOURCE_LABELS[item["source"]]
            assert set(item) == {
                "source",
                "label",
                "configurable",  # 8a-6：重复规则可切换（只有商品资料、博主为真）
                "can_view",
                "can_upload",
                "can_map",
                "can_resolve",
            }
            assert item["configurable"] is (item["source"] in {STYLE_SKU, BLOGGER})

    def test_operations_flags(self, registered: None) -> None:
        by_source = {i["source"]: i for i in access.describe_access(_role_perms("operations"))}
        assert by_source[STYLE_SKU] == {
            "source": STYLE_SKU,
            "label": "商品资料",
            "configurable": True,
            "can_view": True,
            "can_upload": True,
            "can_map": True,
            "can_resolve": True,
        }
        blogger = by_source[BLOGGER]
        assert (blogger["can_view"], blogger["can_upload"], blogger["can_resolve"]) == (
            True,
            False,
            False,
        )
        assert by_source["qianniu"]["can_upload"] is False

    def test_pr_flags(self, registered: None) -> None:
        by_source = {i["source"]: i for i in access.describe_access(_role_perms("pr"))}
        style = by_source[STYLE_SKU]
        assert (style["can_view"], style["can_upload"], style["can_map"], style["can_resolve"]) == (
            True,
            False,
            False,
            False,
        )
        assert by_source[BLOGGER]["can_resolve"] is True
        assert by_source["manual_promotion"]["can_upload"] is True
