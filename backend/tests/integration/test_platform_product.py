"""U10b 集成测试：平台商品映射 CRUD + 反查 + 引用校验 + 改报表口径字段清汇总覆盖（7e-9）。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.product.platform_product_schemas import (
    PlatformProductCreate,
    PlatformProductUpdate,
)
from app.modules.product.platform_product_service import (
    PlatformProductConflictError,
    PlatformProductService,
)

_SEED_COVERAGE_SQL = text(
    """
    INSERT INTO report_summary_coverage
      (id, tenant_id, stat_date, refreshed_at, created_at, updated_at)
    SELECT gen_random_uuid(), CAST(:t AS uuid), d::date, NOW(), NOW(), NOW()
    FROM generate_series(DATE '2026-03-01', DATE '2026-03-03', interval '1 day') AS d
    """
)


async def _seed_coverage(session: AsyncSession, tenant_id: Any) -> None:
    await session.execute(_SEED_COVERAGE_SQL, {"t": tenant_id})


async def _coverage(session: AsyncSession, tenant_id: Any) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM report_summary_coverage WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).scalar_one()
    )


async def _goods(session: AsyncSession, tenant_id: Any, styles: list[Any]) -> Any:
    from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

    g = GoodsMain(tenant_id=tenant_id, goods_code=f"G{uuid4().hex[:8]}", goods_title="商品")
    session.add(g)
    await session.flush()
    for s in styles:
        session.add(GoodsStyleItem(tenant_id=tenant_id, goods_main_id=g.id, style_id=s.id))
    await session.flush()
    return g


@pytest.mark.integration
@pytest.mark.asyncio
class TestPlatformProduct:
    async def test_create_success(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="123456", style_id=style.id),
                user.id,
            )
            assert resp.platform == "qianniu"
            assert resp.platform_id == "123456"
            assert resp.style_id == style.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_409(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="DUP1", style_id=style.id),
                user.id,
            )
            with pytest.raises(PlatformProductConflictError):
                await svc.create(
                    PlatformProductCreate(
                        platform="qianniu", platform_id="DUP1", style_id=style.id
                    ),
                    user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_find_hit_and_miss(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(platform="douyin", platform_id="D1", style_id=style.id),
                user.id,
            )
            assert await svc.find_by_platform_id("douyin", "D1") is not None
            assert await svc.find_by_platform_id("douyin", "MISSING") is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_invalid_style_422(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            with pytest.raises(ValidationError):
                await svc.create(
                    PlatformProductCreate(platform="qianniu", platform_id="X1", style_id=uuid4()),
                    user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_delete(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="DEL1", style_id=style.id),
                user.id,
            )
            await svc.delete(resp.id, user.id)
            assert await svc.find_by_platform_id("qianniu", "DEL1") is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_response_carries_goods_short_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """平台链接页靠商品名认链接：单条返回与列表两条读取路径都要带上简称。"""
        from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            goods_code = f"G{uuid4().hex[:8]}"
            goods = GoodsMain(
                tenant_id=tenant_a.id,
                goods_code=goods_code,
                goods_title="很长的商品全称",
                short_name="短名",
            )
            session.add(goods)
            await session.flush()
            session.add(
                GoodsStyleItem(tenant_id=tenant_a.id, goods_main_id=goods.id, style_id=style.id)
            )
            await session.flush()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)

            created = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="SN1", style_id=style.id),
                user.id,
            )
            assert created.goods_main_id == goods.id
            assert created.goods_short_name == "短名"
            assert created.goods_title == "很长的商品全称"
            assert created.goods_is_suit is False
            assert created.style_code == style.style_code

            items, total = await svc.list_detailed(tenant_id=tenant_a.id, goods_main_id=goods.id)
            assert total == 1
            assert items[0].goods_short_name == "短名"
            assert items[0].goods_title == "很长的商品全称"

            # AC 62（补充 2）：界面不显示商品编码，但平台链接列表按商品编码搜仍命中
            items, total = await svc.list_detailed(tenant_id=tenant_a.id, keyword=goods_code)
            assert total == 1
            assert items[0].platform_id == "SN1"
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestPlatformLinkInvalidatesSummary:
    """7e-9：启停 / 改渠道 / 改归属 / 改款式 / 删除 → 同事务清汇总覆盖；只改名称、新建不清。

    覆盖还在，读取就走汇总表，里面是旧渠道 / 旧归属 / 已停用链接的数字，与实时分叉。
    """

    async def _setup(
        self, session: AsyncSession, tenant_a: Any, factory: Any, role: Any, product_factory: Any
    ) -> tuple[PlatformProductService, Any, Any, dict[str, Any]]:
        s1 = await product_factory.style()
        s2 = await product_factory.style()
        # g1 含两个款式：改款式时归属仍有效、不跟着变，只测款式这一个字段
        g1 = await _goods(session, tenant_a.id, [s1, s2])
        g2 = await _goods(session, tenant_a.id, [s1])
        user = await factory.user(tenant_a, roles=[role])
        svc = PlatformProductService(session)
        created = await svc.create(
            PlatformProductCreate(
                platform="qianniu",
                platform_id=f"INV{uuid4().hex[:8]}",
                style_id=s1.id,
                goods_main_id=g1.id,
                title="旧名称",
            ),
            user.id,
        )
        assert created.goods_main_id == g1.id
        await _seed_coverage(session, tenant_a.id)
        assert await _coverage(session, tenant_a.id) == 3
        return svc, user, created, {"s2": s2.id, "g1": g1.id, "g2": g2.id}

    @pytest.mark.parametrize("field", ["is_active", "channel", "goods_main_id", "style_id"])
    async def test_report_field_change_clears_coverage(
        self,
        field: str,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc, user, created, ids = await self._setup(
                session, tenant_a, factory, admin_role, product_factory
            )
            await _seed_coverage(session, tenant_b.id)
            payload = {
                "is_active": PlatformProductUpdate(is_active=False),
                "channel": PlatformProductUpdate(channel="直播"),
                "goods_main_id": PlatformProductUpdate(goods_main_id=ids["g2"]),
                "style_id": PlatformProductUpdate(style_id=ids["s2"]),
            }[field]
            resp = await svc.update(created.id, payload, user.id)
            # 场景有效性：确实只动了这一个口径字段
            changed = {
                k
                for k, old, new in (
                    ("is_active", created.is_active, resp.is_active),
                    ("channel", created.channel, resp.channel),
                    ("goods_main_id", created.goods_main_id, resp.goods_main_id),
                    ("style_id", created.style_id, resp.style_id),
                )
                if old != new
            }
            assert changed == {field}
            assert await _coverage(session, tenant_a.id) == 0
            assert await _coverage(session, tenant_b.id) == 3
        finally:
            tenant_id_ctx.reset(token)

    async def test_title_only_or_same_values_keep_coverage(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc, user, created, ids = await self._setup(
                session, tenant_a, factory, admin_role, product_factory
            )
            resp = await svc.update(created.id, PlatformProductUpdate(title="新名称"), user.id)
            assert resp.title == "新名称"
            # 传了口径字段但值没变：不算改动
            await svc.update(
                created.id,
                PlatformProductUpdate(is_active=True, channel="普通", goods_main_id=ids["g1"]),
                user.id,
            )
            assert await _coverage(session, tenant_a.id) == 3
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_keeps_coverage(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """新链接没有历史日报，不用清。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc, user, _, ids = await self._setup(
                session, tenant_a, factory, admin_role, product_factory
            )
            await svc.create(
                PlatformProductCreate(
                    platform="qianniu", platform_id=f"NEW{uuid4().hex[:8]}", style_id=ids["s2"]
                ),
                user.id,
            )
            assert await _coverage(session, tenant_a.id) == 3
        finally:
            tenant_id_ctx.reset(token)

    async def test_delete_clears_coverage(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc, user, created, _ = await self._setup(
                session, tenant_a, factory, admin_role, product_factory
            )
            await svc.delete(created.id, user.id)
            assert await svc.find_by_platform_id("qianniu", created.platform_id) is None
            assert await _coverage(session, tenant_a.id) == 0
        finally:
            tenant_id_ctx.reset(token)
