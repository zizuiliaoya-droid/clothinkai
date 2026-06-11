"""U09 core 字段级权限注册表单元测试。

覆盖（替代已删除的 4 个 legacy 字段权限测试）：
- can_read_field / can_write_field：角色默认 / grant / revoke / admin 通配 / 不在注册表
- field_filter：移除不可读字段（移除非 null）
- FieldPermissionContext 构造
- 4 模块字段角色矩阵行为兼容（值迁移自 legacy）
"""

from __future__ import annotations

import pytest

from app.core.security.field_permissions import (
    FIELD_PERMISSION_REGISTRY,
    FieldPermissionContext,
    FieldRule,
    can_read_field,
    can_write_field,
    field_filter,
)


def _ctx(
    roles: set[str],
    *,
    grants: set[str] | None = None,
    revokes: set[str] | None = None,
    superuser: bool = False,
) -> FieldPermissionContext:
    return FieldPermissionContext(
        role_codes=frozenset(roles),
        grants=frozenset(grants or set()),
        revokes=frozenset(revokes or set()),
        is_superuser=superuser,
    )


@pytest.mark.unit
class TestRegistryValues:
    def test_sku_price_roles(self) -> None:
        rule = FIELD_PERMISSION_REGISTRY["sku"]["cost_price"]
        assert rule.visible_roles == frozenset({"admin", "merchandiser", "finance"})
        assert rule.writable_roles == frozenset({"admin", "merchandiser", "finance"})

    def test_blogger_quote_finance_readonly(self) -> None:
        rule = FIELD_PERMISSION_REGISTRY["blogger"]["quote"]
        assert "finance" in rule.visible_roles
        assert "finance" not in rule.writable_roles

    def test_blogger_contact_excludes_finance(self) -> None:
        assert "finance" not in FIELD_PERMISSION_REGISTRY["blogger"]["wechat"].visible_roles

    def test_settlement_amount_no_write(self) -> None:
        assert FIELD_PERMISSION_REGISTRY["settlement"]["amount"].writable_roles == frozenset()

    def test_rule_is_frozen(self) -> None:
        with pytest.raises(Exception):
            FieldRule(frozenset()).visible_roles = frozenset({"x"})  # type: ignore[misc]


@pytest.mark.unit
class TestCanReadField:
    @pytest.mark.parametrize(
        "roles,expected",
        [
            ({"admin"}, True),
            ({"merchandiser"}, True),
            ({"finance"}, True),
            ({"pr"}, False),
            ({"designer"}, False),
            (set(), False),
        ],
    )
    def test_sku_cost_price_by_role(self, roles: set[str], expected: bool) -> None:
        assert can_read_field("sku", "cost_price", _ctx(roles)) is expected

    def test_not_in_registry_always_true(self) -> None:
        assert can_read_field("sku", "base_price", _ctx(set())) is True
        assert can_read_field("unknown", "x", _ctx(set())) is True

    def test_superuser_always_true(self) -> None:
        assert can_read_field("sku", "cost_price", _ctx(set(), superuser=True)) is True

    def test_grant_overrides_role(self) -> None:
        ctx = _ctx({"pr"}, grants={"field.sku.cost_price:read"})
        assert can_read_field("sku", "cost_price", ctx) is True

    def test_revoke_beats_grant_and_role(self) -> None:
        ctx = _ctx(
            {"admin", "finance"},
            grants={"field.sku.cost_price:read"},
            revokes={"field.sku.cost_price:read"},
        )
        # admin role_code matches but is_superuser False here; revoke wins
        assert can_read_field("sku", "cost_price", ctx) is False

    def test_blogger_wechat_finance_denied(self) -> None:
        assert can_read_field("blogger", "wechat", _ctx({"finance"})) is False


@pytest.mark.unit
class TestCanWriteField:
    def test_finance_can_read_not_write_blogger_quote(self) -> None:
        ctx = _ctx({"finance"})
        assert can_read_field("blogger", "quote", ctx) is True
        assert can_write_field("blogger", "quote", ctx) is False

    def test_settlement_payment_amount_writable_roles(self) -> None:
        assert can_write_field("settlement", "payment_amount", _ctx({"pr_manager"})) is True
        assert can_write_field("settlement", "payment_amount", _ctx({"finance"})) is False

    def test_settlement_amount_no_write_by_role(self) -> None:
        # writable_roles 为空 → 任何角色（非超管）都不可写
        assert can_write_field("settlement", "amount", _ctx({"admin"})) is False
        assert can_write_field("settlement", "amount", _ctx({"pr_manager"})) is False
        # 超管通配仍可写
        assert can_write_field("settlement", "amount", _ctx(set(), superuser=True)) is True

    def test_write_grant_override(self) -> None:
        ctx = _ctx({"designer"}, grants={"field.promotion.quote_amount:write"})
        assert can_write_field("promotion", "quote_amount", ctx) is True


@pytest.mark.unit
class TestFieldFilter:
    def test_removes_unreadable_keys(self) -> None:
        data = {"sku_code": "S1", "cost_price": "100", "purchase_price": "80"}
        out = field_filter("sku", data, _ctx({"pr"}))
        assert "cost_price" not in out
        assert "purchase_price" not in out
        assert out["sku_code"] == "S1"  # 非注册表字段保留

    def test_keeps_readable_keys(self) -> None:
        data = {"cost_price": "100"}
        out = field_filter("sku", data, _ctx({"finance"}))
        assert out["cost_price"] == "100"
