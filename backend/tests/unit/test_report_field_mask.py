"""7e R1：report/field_mask.py（报表视图设计第 4 节字段级 F2 / F3）。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security.field_permissions import FieldPermissionContext
from app.modules.report import field_mask
from app.modules.report.field_mask import (
    AGGREGATE_REPORTS,
    AggregateReport,
    aggregate_promo_amounts_visible,
    promotion_amounts_visible,
)

_QUOTE = "field.promotion.quote_amount:read"
_COST = "field.promotion.cost_snapshot:read"


def _ctx(
    *roles: str,
    grants: frozenset[str] = frozenset(),
    revokes: frozenset[str] = frozenset(),
    superuser: bool = False,
) -> FieldPermissionContext:
    return FieldPermissionContext(
        role_codes=frozenset(roles), grants=grants, revokes=revokes, is_superuser=superuser
    )


@pytest.mark.unit
class TestPromotionAmountsVisible:
    """F2 逐单金额：服务费看 quote_amount；站外推广成本要 quote_amount 与 cost_snapshot 都可读。"""

    def test_superuser_sees_both(self) -> None:
        assert promotion_amounts_visible(_ctx(superuser=True)) == (True, True)

    @pytest.mark.parametrize("role", ["admin", "finance", "pr", "pr_manager"])
    def test_amount_roles_see_both(self, role: str) -> None:
        assert promotion_amounts_visible(_ctx(role)) == (True, True)

    @pytest.mark.parametrize("role", ["operations", "warehouse", "merchandiser"])
    def test_other_roles_see_none(self, role: str) -> None:
        assert promotion_amounts_visible(_ctx(role)) == (False, False)

    def test_cost_snapshot_revoked_hides_total_cost_only(self) -> None:
        ctx = _ctx("finance", revokes=frozenset({_COST}))
        assert promotion_amounts_visible(ctx) == (True, False)

    def test_quote_revoked_hides_both(self) -> None:
        # 站外成本减去样品成本就是服务费：quote 读不到时站外成本也不给
        ctx = _ctx("pr", revokes=frozenset({_QUOTE}))
        assert promotion_amounts_visible(ctx) == (False, False)

    def test_operations_with_personal_grants_sees_both(self) -> None:
        ctx = _ctx("operations", grants=frozenset({_QUOTE, _COST}))
        assert promotion_amounts_visible(ctx) == (True, True)

    def test_operations_with_quote_grant_only(self) -> None:
        ctx = _ctx("operations", grants=frozenset({_QUOTE}))
        assert promotion_amounts_visible(ctx) == (True, False)


@pytest.mark.unit
class TestAggregatePromoAmountsVisible:
    """F3 聚合金额：按报表读权限给，不受逐单字段权限约束（决定 12）。"""

    @pytest.mark.parametrize("report", sorted(AGGREGATE_REPORTS))
    @pytest.mark.parametrize(
        "ctx",
        [
            _ctx("operations"),
            _ctx("warehouse"),
            _ctx("finance", revokes=frozenset({_QUOTE, _COST})),
            _ctx("admin", superuser=True),
        ],
    )
    def test_always_visible_in_r1(
        self, report: AggregateReport, ctx: FieldPermissionContext
    ) -> None:
        assert aggregate_promo_amounts_visible(report, ctx) is True

    def test_reports_cover_design_list(self) -> None:
        assert AGGREGATE_REPORTS == {"work_progress", "bi", "production", "store_daily"}


@pytest.mark.unit
async def test_build_field_perm_context_wraps_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """每请求一次：包 core 的同名函数，传当前会话上的两个 repo。"""
    seen: dict[str, object] = {}
    expected = _ctx("finance")

    async def fake(user_id: object, role_repo: object, perm_repo: object) -> FieldPermissionContext:
        seen.update(user_id=user_id, role_repo=role_repo, perm_repo=perm_repo)
        return expected

    monkeypatch.setattr(field_mask, "_core_build", fake)
    session = object()
    uid = uuid4()
    assert await field_mask.build_field_perm_context(uid, session) is expected  # type: ignore[arg-type]
    assert seen["user_id"] == uid
    assert type(seen["role_repo"]).__name__ == "RoleRepository"
    assert type(seen["perm_repo"]).__name__ == "PermissionRepository"
    assert getattr(seen["role_repo"], "_session", None) is session
    assert getattr(seen["perm_repo"], "_session", None) is session
