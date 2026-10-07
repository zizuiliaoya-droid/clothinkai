"""季节选项（8a-3，FR-3.6、3.7，设计 §8.3，AC 19、20）。

- ``list_season_options``：字典 season 启用值（``sort_order, value``）在前，再接商品上出现过、
  字典里没有的值（按值降序），去重；停用字典值、已删商品、空白值、别的租户都不进来
- 报表侧 ``GET /api/reports/season-options`` 只要 ``report.production:read``：主管 200 且非空，
  跟单（没有报表读权限）403——不再依赖 ``/api/dict-items``（C-02）
- 商品侧 ``GET /api/goods/season-options`` 要 ``product.goods:read``，声明在 ``/{goods_id}`` 之前
- 商品能保存季节并读回（AC 20 后半）
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.dict_models import DictItem
from app.modules.product.goods_models import GoodsMain
from app.modules.product.goods_schemas import GoodsMainCreate, GoodsMainUpdate, GoodsStyleItemIn
from app.modules.product.goods_service import GoodsService
from app.modules.product.season_options import list_season_options

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _route_permissions(path: str, method: str = "GET") -> set[tuple[str, str]]:
    """从路由依赖里取出 require_permission 闭包的 (scope, action)。"""
    from fastapi.routing import APIRoute

    from app.main import app

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            perms: set[tuple[str, str]] = set()
            for dep in route.dependencies:
                fn = dep.dependency
                if fn is None or not inspect.isfunction(fn):
                    continue
                nonlocals = inspect.getclosurevars(fn).nonlocals
                if "scope" in nonlocals and "action" in nonlocals:
                    perms.add((str(nonlocals["scope"]), str(nonlocals["action"])))
            return perms
    raise AssertionError(f"路由不存在：{method} {path}")


async def _seed_options(session: AsyncSession, tenant: Any, other_tenant: Any) -> None:
    session.add_all(
        [
            DictItem(tenant_id=tenant.id, dict_type="season", value="2026秋", sort_order=2),
            DictItem(tenant_id=tenant.id, dict_type="season", value="共用", sort_order=1),
            DictItem(tenant_id=tenant.id, dict_type="season", value="2026春", sort_order=1),
            # 停用的字典值不在字典段；商品上还挂着它，就出现在商品段
            DictItem(
                tenant_id=tenant.id,
                dict_type="season",
                value="2025冬",
                sort_order=0,
                is_active=False,
            ),
            # 别的字典类型不进来
            DictItem(tenant_id=tenant.id, dict_type="color", value="黑色", sort_order=0),
        ]
    )

    def goods(
        code: str, season: str | None, *, tenant_id: Any = None, deleted: bool = False
    ) -> GoodsMain:
        return GoodsMain(
            tenant_id=tenant_id or tenant.id,
            goods_code=code,
            goods_title=f"{code} 商品",
            season=season,
            is_deleted=deleted,
        )

    session.add_all(
        [
            goods("SO-1", "2026秋"),  # 字典里已有 → 不重复
            goods("SO-2", "2024夏"),
            goods("SO-3", "2024夏"),  # 同值两件 → 只出一次
            goods("SO-4", "2025冬"),
            goods("SO-5", "   "),  # 空白不算
            goods("SO-6", None),
            goods("SO-7", "1999旧", deleted=True),  # 已删商品不算
        ]
    )
    await session.flush()
    # 别的租户的字典值与商品季节不进来（flush 要在对方租户上下文里）
    token = tenant_id_ctx.set(other_tenant.id)
    try:
        session.add_all(
            [
                DictItem(
                    tenant_id=other_tenant.id, dict_type="season", value="别家季", sort_order=0
                ),
                goods("SO-8", "别家商品季", tenant_id=other_tenant.id),
            ]
        )
        await session.flush()
    finally:
        tenant_id_ctx.reset(token)


EXPECTED = ["2026春", "共用", "2026秋", "2025冬", "2024夏"]


class TestListSeasonOptions:
    async def test_dict_first_then_goods_values(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any
    ) -> None:
        """AC 20：同时含字典启用值与只出现在商品上的值；字典按 (sort_order, value)，商品段按值降序。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_options(session, tenant_a, tenant_b)
            assert await list_season_options(session, tenant_a.id) == EXPECTED
        finally:
            tenant_id_ctx.reset(token)

    async def test_goods_only_values_without_dict(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        """字典里一个季节都没配时，商品上已有的值仍能选到（商品页的季节筛选也要能选到，FR-3.7）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            session.add(
                GoodsMain(
                    tenant_id=tenant_a.id, goods_code="SO-ONLY", goods_title="x", season="2027春"
                )
            )
            await session.flush()
            assert await list_season_options(session, tenant_a.id) == ["2027春"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_empty(self, session: AsyncSession, tenant_a: Any) -> None:
        assert await list_season_options(session, tenant_a.id) == []


class TestSeasonOptionsRoutes:
    def test_report_route_only_needs_production_read(self) -> None:
        """AC 19：接口只要求投产报表读权限。"""
        assert _route_permissions("/api/reports/season-options") == {("report.production", "read")}

    def test_goods_route_needs_goods_read(self) -> None:
        assert _route_permissions("/api/goods/season-options") == {("product.goods", "read")}

    def test_goods_route_declared_before_goods_id(self) -> None:
        from app.modules.product.goods_api import router

        paths = [getattr(r, "path", "") for r in router.routes]
        assert paths.index("/api/goods/season-options") < paths.index("/api/goods/{goods_id}")


class TestSeasonOptionsHttp:
    """真实路由 + 默认角色的有效权限（照 test_goods_crud::TestBrandOptionsApi）。"""

    async def _get(
        self, session: AsyncSession, factory: Any, tenant: Any, role_code: str, path: str
    ) -> Any:
        from httpx import ASGITransport, AsyncClient

        from app.core.db import get_session
        from app.main import app
        from app.modules.auth.deps import get_current_perms, get_current_user_active
        from app.modules.auth.models import Role
        from app.modules.auth.service import AuthService

        async def _session_override() -> AsyncIterator[AsyncSession]:
            yield session

        role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await factory.user(tenant, roles=[role])
        perms = await AuthService(session).load_effective_permissions(user.id)
        try:
            app.dependency_overrides[get_session] = _session_override
            app.dependency_overrides[get_current_user_active] = lambda: user
            app.dependency_overrides[get_current_perms] = lambda: perms
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                return await c.get(path)
        finally:
            for dep in (get_session, get_current_user_active, get_current_perms):
                app.dependency_overrides.pop(dep, None)

    async def test_pr_manager_gets_report_options(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any, factory: Any
    ) -> None:
        """AC 19：主管（没有 product:read，原来的 /api/dict-items 下拉为空）→ 200 且非空。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_options(session, tenant_a, tenant_b)
            resp = await self._get(
                session, factory, tenant_a, "pr_manager", "/api/reports/season-options"
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"items": EXPECTED}
            # 对照：主管确实拿不到字典接口（C-02 的成因）
            dict_resp = await self._get(
                session, factory, tenant_a, "pr_manager", "/api/dict-items?dict_type=season"
            )
            assert dict_resp.status_code == 403
        finally:
            tenant_id_ctx.reset(token)

    async def test_merchandiser_report_options_forbidden(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """AC 19：跟单没有报表读权限 → 403。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            resp = await self._get(
                session, factory, tenant_a, "merchandiser", "/api/reports/season-options"
            )
            assert resp.status_code == 403
        finally:
            tenant_id_ctx.reset(token)

    async def test_goods_options_for_merchandiser_and_operations(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any, factory: Any
    ) -> None:
        """商品页的季节选项：持 product.goods:read 的跟单、运营 200，结果与报表侧一致。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_options(session, tenant_a, tenant_b)
            for role_code in ("merchandiser", "operations"):
                resp = await self._get(
                    session, factory, tenant_a, role_code, "/api/goods/season-options"
                )
                assert resp.status_code == 200, (role_code, resp.text)
                assert resp.json() == {"items": EXPECTED}
            # PR 主管没有商品读权限
            resp = await self._get(
                session, factory, tenant_a, "pr_manager", "/api/goods/season-options"
            )
            assert resp.status_code == 403
        finally:
            tenant_id_ctx.reset(token)


class TestGoodsSeasonSaved:
    async def test_goods_season_saved_and_read_back(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """AC 20 后半：商品能选季节并保存、读回；选过的值随即出现在季节选项里。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="SO-S1")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="SO-G1",
                    goods_title="季节商品",
                    season="2026秋",
                    items=[GoodsStyleItemIn(style_id=style.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert created.season == "2026秋"
            updated = await svc.update(
                created.id, GoodsMainUpdate(season="2027春"), user_id=user.id
            )
            assert updated.season == "2027春"
            assert (await svc.get(created.id)).season == "2027春"
            assert await list_season_options(session, tenant_a.id) == ["2027春"]
        finally:
            tenant_id_ctx.reset(token)
