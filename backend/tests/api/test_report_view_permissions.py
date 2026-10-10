"""7e R1 报表视图权限的路由层契约（报表视图设计第 4 节、5.1）。

真实路由 + 迁移 seed 的真实角色（``flow_users``）：

- 工作进度：``require_any_permission(read, read_own)``；只持 ``read_own`` 的 PR 只看到本人那一行
  （service 层 ``resolve_pr_scope`` 强制）；区间参数 ``preset`` / ``date_from`` / ``date_to``，
  旧参数 ``month`` 换算成整月；F3 聚合金额运营与管理员相同
- 发文进度 4 个接口：``require_any_permission(read, read_own)``；只持 ``read_own`` 的 PR 只看到
  本人的单（``PrScope`` 下推到 SQL，摘要 / 卡片 total 也只数本人）
- BI 三个端点挂 ``report.bi:read``（不再挂 ``report.production:read``）；推广汇总 4 个金额过 F3
- 导出：路由仍要 ``report.export:read``，service 再按报表类型判对应读权限（缺 → 403）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

# 场景放在上个月：整月都在过去，日期与「今天」无关的计数稳定
_TODAY = get_today()
_M_HI = _TODAY.replace(day=1) - timedelta(days=1)
_M_LO = _M_HI.replace(day=1)
_MONTH = f"{_M_LO:%Y-%m}"
_WP = "/api/reports/work-progress"


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any = None) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限。"""
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
async def seeded(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any
) -> Any:
    """上个月：pr 两单、pr2 一单、未分配一单，都带样品成本（工作进度的 ``cost``）。"""
    style = await product_factory.style(style_code=f"RV{uuid4().hex[:8]}")
    blogger = await blogger_factory.blogger()

    async def promo(**kw: Any) -> None:
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            internal_code=f"RV{uuid4().hex[:10].upper()}",
            cooperation_date=_M_LO + timedelta(days=2),
            **kw,
        )

    await promo(pr=flow_users.pr, cost_snapshot=Decimal("30.00"), publish_status="已发布")
    await promo(pr=flow_users.pr, cost_snapshot=Decimal("12.50"))
    await promo(pr=flow_users.pr2, cost_snapshot=Decimal("40.00"))
    await promo(cost_snapshot=Decimal("7.00"))
    return flow_users


@pytest.mark.usefixtures("tenant_ctx")
class TestWorkProgress:
    async def test_pr_sees_only_self(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.pr, "GET", f"{_WP}?month={_MONTH}")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["pr_id"] for r in rows] == [str(seeded.pr.id)]
        assert rows[0]["quote_count"] == 2

    async def test_finance_sees_all(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.finance, "GET", f"{_WP}?month={_MONTH}")
        assert resp.status_code == 200, resp.text
        assert {r["pr_id"] for r in resp.json()} == {str(seeded.pr.id), str(seeded.pr2.id), None}

    async def test_month_equals_custom_range(self, session: AsyncSession, seeded: Any) -> None:
        by_month = await _call(session, seeded.pr_manager, "GET", f"{_WP}?month={_MONTH}")
        by_range = await _call(
            session,
            seeded.pr_manager,
            "GET",
            f"{_WP}?preset=custom&date_from={_M_LO}&date_to={_M_HI}",
        )
        assert by_month.status_code == by_range.status_code == 200
        assert by_month.json() == by_range.json()
        # 场景有效性：三行（pr / pr2 / 未分配）
        assert len(by_month.json()) == 3

    async def test_default_preset_is_this_month(self, session: AsyncSession, seeded: Any) -> None:
        bare = await _call(session, seeded.pr_manager, "GET", _WP)
        this_month = await _call(session, seeded.pr_manager, "GET", f"{_WP}?preset=this_month")
        assert bare.status_code == this_month.status_code == 200
        assert bare.json() == this_month.json()

    async def test_operations_aggregate_cost_equals_admin(
        self, session: AsyncSession, seeded: Any
    ) -> None:
        """F3：运营读不到逐单 ``cost_snapshot``，聚合的 ``cost`` / ``cpl`` 照样与管理员相同。"""
        ops = await _call(session, seeded.operations, "GET", f"{_WP}?month={_MONTH}")
        admin = await _call(session, seeded.admin, "GET", f"{_WP}?month={_MONTH}")
        assert ops.status_code == admin.status_code == 200
        assert ops.json() == admin.json()
        costs = {r["pr_id"]: r["cost"] for r in ops.json()}
        assert costs[str(seeded.pr.id)] == "42.50"

    async def test_warehouse_forbidden(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.warehouse, "GET", f"{_WP}?month={_MONTH}")
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ("preset=foo", "REPORT_INVALID_TIME_PRESET"),
            (
                f"preset=custom&date_from={_M_LO}&date_to={_M_LO + timedelta(days=367)}",
                "REPORT_INVALID_TIME_RANGE",
            ),
            (f"preset=custom&date_from={_M_HI}&date_to={_M_LO}", "REPORT_INVALID_TIME_RANGE"),
            ("month=2026-13", "REPORT_INVALID_TIME_RANGE"),
        ],
        ids=["bad_preset", "span_367", "from_after_to", "bad_month"],
    )
    async def test_invalid_range_422(
        self, session: AsyncSession, flow_users: Any, query: str, code: str
    ) -> None:
        resp = await _call(session, flow_users.pr_manager, "GET", f"{_WP}?{query}")
        assert (resp.status_code, resp.json()["code"]) == (422, code)

    async def test_span_366_ok(self, session: AsyncSession, flow_users: Any) -> None:
        hi = _M_LO + timedelta(days=366)
        resp = await _call(
            session,
            flow_users.pr_manager,
            "GET",
            f"{_WP}?preset=custom&date_from={_M_LO}&date_to={hi}",
        )
        assert resp.status_code == 200, resp.text


_PP = "/api/reports/publish-progress"
_PP_RANGE = f"preset=custom&date_from={_M_LO}&date_to={_M_HI}"


@pytest.fixture
async def pp_seeded(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any
) -> Any:
    """上个月：款 A 上 pr 两单（一单已发布）、pr2 一单、未分配一单；款 B 只有 pr2 一单。

    返回 ``(users, style_a, style_b, 非 pr 的单 id)``。
    """
    style_a = await product_factory.style(style_code=f"PA{uuid4().hex[:8]}")
    style_b = await product_factory.style(style_code=f"PB{uuid4().hex[:8]}")
    blogger = await blogger_factory.blogger()

    async def promo(style: Any, day: int, **kw: Any) -> Any:
        return await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            internal_code=f"PP{uuid4().hex[:10].upper()}",
            cooperation_date=_M_LO + timedelta(days=day),
            **kw,
        )

    pr = flow_users.pr
    await promo(
        style_a,
        2,
        pr=pr,
        quote_amount=Decimal("300.00"),
        publish_status="已发布",
        like_count=120,
    )
    await promo(style_a, 20, pr=pr, quote_amount=Decimal("150.00"))
    others = [
        await promo(
            style_a,
            3,
            pr=flow_users.pr2,
            quote_amount=Decimal("500.00"),
            publish_status="已发布",
            like_count=900,
        ),
        await promo(style_a, 21, quote_amount=Decimal("80.00")),
        await promo(style_b, 4, pr=flow_users.pr2, quote_amount=Decimal("60.00")),
    ]
    return flow_users, style_a, style_b, [p.id for p in others]


def _pp_paths(style_a: Any, style_b: Any) -> dict[str, str]:
    return {
        "summary": f"{_PP}/summary?{_PP_RANGE}",
        "cards": f"{_PP}/cards?{_PP_RANGE}",
        "by_pr_a": f"{_PP}/styles/{style_a.id}/by-pr?{_PP_RANGE}",
        "by_pr_b": f"{_PP}/styles/{style_b.id}/by-pr?{_PP_RANGE}",
        "by_time_a": f"{_PP}/styles/{style_a.id}/by-time?{_PP_RANGE}",
    }


async def _pp_all(session: AsyncSession, user: Any, paths: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, path in paths.items():
        resp = await _call(session, user, "GET", path)
        assert resp.status_code == 200, (name, resp.text)
        out[name] = resp.json()
    return out


@pytest.mark.usefixtures("tenant_ctx")
class TestPublishProgress:
    async def test_pr_sees_only_self(self, session: AsyncSession, pp_seeded: Any) -> None:
        """pr 的 4 个接口 = 只有 pr 的单时主管看到的数字（逐字相等）。"""
        users, style_a, style_b, others = pp_seeded
        paths = _pp_paths(style_a, style_b)
        mine = await _pp_all(session, users.pr, paths)
        full = await _pp_all(session, users.pr_manager, paths)

        # 场景有效性：主管看到的比 pr 多（别人的单确实在区间里）
        assert full["summary"]["quote_count"] == 5
        assert full["cards"]["total"] == 2
        assert len(full["by_pr_a"]) == 3
        assert sum(p["quote_count"] for p in full["by_time_a"]) == 4

        # pr 只剩本人：数字非零，且只有本人那一行
        assert mine["summary"]["quote_count"] == 2
        assert mine["summary"]["publish_count"] == 1
        assert mine["summary"]["quote_amount"] == "450.00"
        assert mine["summary"]["like_count"] > 0
        assert mine["cards"]["total"] == 1
        assert [c["style_id"] for c in mine["cards"]["items"]] == [str(style_a.id)]
        assert [r["pr_id"] for r in mine["by_pr_a"]] == [str(users.pr.id)]
        assert mine["by_pr_b"] == []
        assert sum(p["quote_count"] for p in mine["by_time_a"]) == 2

        # 停用别人的单后，主管看到的 = pr 之前看到的
        await session.execute(
            text("UPDATE promotion SET is_active = false WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": others},
        )
        only_mine = await _pp_all(session, users.pr_manager, paths)
        assert only_mine == mine

    async def test_admin_sees_all(self, session: AsyncSession, pp_seeded: Any) -> None:
        users, style_a, _, _ = pp_seeded
        resp = await _call(session, users.admin, "GET", f"{_PP}/summary?{_PP_RANGE}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["quote_count"] == 5

    @pytest.mark.parametrize("role", ["finance", "warehouse"])
    async def test_forbidden(self, session: AsyncSession, pp_seeded: Any, role: str) -> None:
        users, style_a, style_b, _ = pp_seeded
        for path in _pp_paths(style_a, style_b).values():
            resp = await _call(session, getattr(users, role), "GET", path)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), path


async def _override(session: AsyncSession, user: Any, scope: str, effect: str) -> None:
    """给用户造一条个人授予 / 撤销（scope 须已入册）。"""
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=user.tenant_id, user_id=user.id, permission_id=perm.id, effect=effect
        )
    )
    await session.flush()


_BI = "/api/reports/bi"
_BI_RANGE = f"preset=custom&date_from={_M_LO}&date_to={_M_HI}"
_PROD = f"/api/reports/production?{_BI_RANGE}"


@pytest.fixture
async def bi_seeded(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any
) -> Any:
    """上个月：已发布 / 未发布 / 已取消各一单，推广汇总 4 个金额都非零。"""
    style = await product_factory.style(style_code=f"BI{uuid4().hex[:8]}")
    blogger = await blogger_factory.blogger()
    for i, (status, amount) in enumerate(
        [("已发布", "300.00"), ("未发布", "150.00"), ("已取消", "80.00")]
    ):
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            internal_code=f"BI{uuid4().hex[:10].upper()}",
            cooperation_date=_M_LO + timedelta(days=i + 1),
            quote_amount=Decimal(amount),
            publish_status=status,
        )
    return flow_users


@pytest.mark.usefixtures("tenant_ctx")
class TestBi:
    async def test_finance_bi_and_layout_but_not_production(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        fin = flow_users.finance
        bi = await _call(session, fin, "GET", f"{_BI}?{_BI_RANGE}")
        assert bi.status_code == 200, bi.text
        get_layout = await _call(session, fin, "GET", f"{_BI}/layout")
        assert get_layout.status_code == 200, get_layout.text
        put_layout = await _call(session, fin, "PUT", f"{_BI}/layout", json={"cards": ["x"]})
        assert put_layout.status_code == 200, put_layout.text
        assert (await _call(session, fin, "GET", f"{_BI}/layout")).json() == {"cards": ["x"]}
        # BI 拆出来后，给财务 BI 不再连带放开投产页
        prod = await _call(session, fin, "GET", _PROD)
        assert (prod.status_code, prod.json()["code"]) == (403, "PERMISSION_DENIED")

    async def test_production_read_alone_is_not_bi(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        wh = flow_users.warehouse
        await _override(session, wh, "report.production:read", "grant")
        # 场景有效性：授予生效（投产页能进）
        assert (await _call(session, wh, "GET", _PROD)).status_code == 200
        for method, path, body in [
            ("GET", f"{_BI}?{_BI_RANGE}", None),
            ("GET", f"{_BI}/layout", None),
            ("PUT", f"{_BI}/layout", {"cards": ["x"]}),
        ]:
            resp = await _call(session, wh, method, path, json=body)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), path

    @pytest.mark.parametrize("role", ["pr", "warehouse"])
    async def test_forbidden(self, session: AsyncSession, flow_users: Any, role: str) -> None:
        resp = await _call(session, getattr(flow_users, role), "GET", f"{_BI}?{_BI_RANGE}")
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")

    async def test_operations_promo_amounts_equal_admin(
        self, session: AsyncSession, bi_seeded: Any
    ) -> None:
        """F3：运营读不到逐单 ``quote_amount``，BI 推广汇总金额照样与管理员相同。"""
        ops = await _call(session, bi_seeded.operations, "GET", f"{_BI}?{_BI_RANGE}")
        admin = await _call(session, bi_seeded.admin, "GET", f"{_BI}?{_BI_RANGE}")
        assert ops.status_code == admin.status_code == 200
        assert ops.json() == admin.json()
        summary = ops.json()["promotion_summary"]
        # 场景有效性：4 个金额都非零
        assert (
            summary["commission_amount"],
            summary["published_spend"],
            summary["unpublished_spend"],
            summary["cancelled_amount"],
        ) == ("530.00", "300.00", "150.00", "80.00")


def _export(kind: str) -> str:
    return f"/api/reports/{kind}/export?{_BI_RANGE}"


@pytest.mark.usefixtures("tenant_ctx")
class TestExport:
    @pytest.mark.parametrize(
        ("kind", "status"),
        [("work-progress", 200), ("production", 403), ("store-daily", 403)],
    )
    async def test_finance_by_report_type(
        self, session: AsyncSession, flow_users: Any, kind: str, status: int
    ) -> None:
        """财务持 ``report.export:read`` + 工作进度 read：能导工作进度，投产 / 店铺 403。"""
        resp = await _call(session, flow_users.finance, "GET", _export(kind))
        assert resp.status_code == status, resp.text
        if status == 403:
            assert resp.json()["code"] == "PERMISSION_DENIED"
            assert resp.json()["details"]["required_action"] == "read"
        else:
            assert resp.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml"
            )

    @pytest.mark.parametrize("kind", ["work-progress", "production", "store-daily"])
    async def test_operations_all_types(
        self, session: AsyncSession, flow_users: Any, kind: str
    ) -> None:
        resp = await _call(session, flow_users.operations, "GET", _export(kind))
        assert resp.status_code == 200, resp.text

    async def test_follows_read_permission(self, session: AsyncSession, flow_users: Any) -> None:
        """判的是对应读权限本身：财务个人授予投产读后，导出投产放行，店铺照旧 403。"""
        fin = flow_users.finance
        before = await _call(session, fin, "GET", _export("production"))
        assert (before.status_code, before.json()["code"]) == (403, "PERMISSION_DENIED")
        assert before.json()["details"]["required_scope"] == "report.production"
        await _override(session, fin, "report.production:read", "grant")
        assert (await _call(session, fin, "GET", _export("production"))).status_code == 200
        store = await _call(session, fin, "GET", _export("store-daily"))
        assert store.json()["details"]["required_scope"] == "report.store_daily"

    async def test_invalid_type_still_400(self, session: AsyncSession, flow_users: Any) -> None:
        """类型先校验：不认识的类型照旧 400，不因为没有对应读权限变成 403。"""
        resp = await _call(session, flow_users.finance, "GET", _export("unknown"))
        assert (resp.status_code, resp.json()["code"]) == (400, "REPORT_EXPORT_TYPE_INVALID")

    @pytest.mark.parametrize("role", ["pr", "warehouse"])
    async def test_without_export_scope(
        self, session: AsyncSession, flow_users: Any, role: str
    ) -> None:
        resp = await _call(session, getattr(flow_users, role), "GET", _export("work-progress"))
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
        assert resp.json()["details"]["required_scope"] == "report.export"
