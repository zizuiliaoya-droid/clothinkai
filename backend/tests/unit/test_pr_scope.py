"""7e R1：report/pr_scope.py 与 auth/deps.require_any_permission（报表视图设计第 4 节）。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import require_any_permission
from app.modules.report.pr_scope import (
    PR_SCOPE_ALL,
    UNASSIGNED,
    PrScope,
    PrScopeKind,
    parse_pr_param,
    resolve_pr_scope,
)

_WP = "report.work_progress"
_ME = UUID("11111111-1111-1111-1111-111111111111")
_OTHER = UUID("22222222-2222-2222-2222-222222222222")


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id=str(_ME), scopes=frozenset(scopes))


@pytest.mark.unit
class TestResolvePrScope:
    @pytest.mark.parametrize("scopes", [("report.work_progress:read",), ("report.*:read",), ("*",)])
    def test_full_follows_requested(self, scopes: tuple[str, ...]) -> None:
        perms = _perms(*scopes)
        assert resolve_pr_scope(perms, _ME, None, _WP) == PR_SCOPE_ALL
        assert resolve_pr_scope(perms, _ME, _OTHER, _WP) == PrScope(PrScopeKind.PR, _OTHER)
        assert resolve_pr_scope(perms, _ME, UNASSIGNED, _WP) == PrScope(PrScopeKind.UNASSIGNED)

    def test_full_wins_over_read_own(self) -> None:
        perms = _perms("report.work_progress:read", "report.work_progress:read_own")
        assert resolve_pr_scope(perms, _ME, None, _WP) == PR_SCOPE_ALL

    @pytest.mark.parametrize("requested", [None, _OTHER, _ME, UNASSIGNED])
    def test_read_own_always_self(self, requested: UUID | str | None) -> None:
        perms = _perms("report.work_progress:read_own")
        scope = resolve_pr_scope(perms, _ME, requested, _WP)  # type: ignore[arg-type]
        assert scope == PrScope(PrScopeKind.PR, _ME)

    def test_read_own_not_matched_by_wildcard_read(self) -> None:
        # report.*:read 不覆盖 read_own（通配按 action 精确匹配）—— 但持 read 本来就看全部
        perms = _perms("report.*:read")
        assert perms.has(_WP, "read") and not perms.has(_WP, "read_own")

    def test_also_full(self) -> None:
        perms = _perms("report.bi:read")
        scope = resolve_pr_scope(perms, _ME, _OTHER, _WP, also_full=(("report.bi", "read"),))
        assert scope == PrScope(PrScopeKind.PR, _OTHER)
        with pytest.raises(PermissionDeniedError):
            resolve_pr_scope(perms, _ME, _OTHER, _WP)

    def test_also_full_beats_read_own(self) -> None:
        perms = _perms("report.bi:read", "report.work_progress:read_own")
        scope = resolve_pr_scope(perms, _ME, None, _WP, also_full=(("report.bi", "read"),))
        assert scope == PR_SCOPE_ALL

    @pytest.mark.parametrize(
        "scopes",
        [(), ("report.publish_progress:read_own",), ("report.production:read",)],
    )
    def test_none_denied(self, scopes: tuple[str, ...]) -> None:
        with pytest.raises(PermissionDeniedError):
            resolve_pr_scope(_perms(*scopes), _ME, None, _WP)

    def test_prefix_is_respected(self) -> None:
        perms = _perms("report.publish_progress:read_own")
        scope = resolve_pr_scope(perms, _ME, None, "report.publish_progress")
        assert scope == PrScope(PrScopeKind.PR, _ME)


@pytest.mark.unit
class TestPrScopeFilters:
    def test_keeps(self) -> None:
        assert all(PR_SCOPE_ALL.keeps(x) for x in (None, _ME, _OTHER))
        mine = PrScope(PrScopeKind.PR, _ME)
        assert mine.keeps(_ME)
        assert not mine.keeps(_OTHER)
        assert not mine.keeps(None)
        unassigned = PrScope(PrScopeKind.UNASSIGNED)
        assert unassigned.keeps(None)
        assert not unassigned.keeps(_ME)

    def test_sql(self) -> None:
        assert PR_SCOPE_ALL.sql("p.pr_id") == ("", {})
        assert PrScope(PrScopeKind.UNASSIGNED).sql("p.pr_id") == (" AND p.pr_id IS NULL", {})
        assert PrScope(PrScopeKind.PR, _ME).sql("pr_id") == (
            " AND pr_id = CAST(:scope_pr_id AS uuid)",
            {"scope_pr_id": _ME},
        )

    @pytest.mark.parametrize(
        ("kind", "pr_id"),
        [(PrScopeKind.PR, None), (PrScopeKind.ALL, _ME), (PrScopeKind.UNASSIGNED, _ME)],
    )
    def test_kind_and_pr_id_consistent(self, kind: PrScopeKind, pr_id: UUID | None) -> None:
        with pytest.raises(ValueError):
            PrScope(kind, pr_id)


@pytest.mark.unit
class TestParsePrParam:
    def test_values(self) -> None:
        assert parse_pr_param(None) is None
        assert parse_pr_param("unassigned") == UNASSIGNED
        assert parse_pr_param(str(_OTHER)) == _OTHER

    @pytest.mark.parametrize("raw", ["", "abc", "UNASSIGNED", "1234", f"{uuid4()}x"])
    def test_invalid_422(self, raw: str) -> None:
        with pytest.raises(ValidationError) as ei:
            parse_pr_param(raw)
        assert ei.value.status_code == 422


_ANY_WP = require_any_permission(
    ("report.work_progress", "read"), ("report.work_progress", "read_own")
).dependency
assert _ANY_WP is not None


@pytest.mark.unit
class TestRequireAnyPermission:
    @pytest.mark.parametrize(
        "scopes",
        [
            ("report.work_progress:read",),
            ("report.work_progress:read_own",),
            ("report.*:read",),
            ("*",),
        ],
    )
    async def test_any_hit_passes(self, scopes: tuple[str, ...]) -> None:
        perms = _perms(*scopes)
        assert await _ANY_WP(perms) is perms

    @pytest.mark.parametrize(
        "scopes", [(), ("report.production:read",), ("report.publish_progress:read_own",)]
    )
    async def test_none_hit_403(self, scopes: tuple[str, ...]) -> None:
        with pytest.raises(PermissionDeniedError) as ei:
            await _ANY_WP(_perms(*scopes))
        assert ei.value.status_code == 403
        assert ei.value.details == {
            "required_any": [
                {"required_scope": "report.work_progress", "required_action": "read"},
                {"required_scope": "report.work_progress", "required_action": "read_own"},
            ]
        }
