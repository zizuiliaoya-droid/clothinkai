"""千牛日报 / 单品站内推广日报 列表筛选与排序。

这两个端点原先只支持分页 + 固定 date desc，用户反馈「尽量做到能筛选的都支持筛选」。
筛选与排序必须落在服务端 —— 日报按天累积，客户端过滤只覆盖当前页。

两个端点共用 ``_apply_range`` / ``_apply_sort``，所以这里重点验证：
区间边界是闭区间、NULL 值不会因为排序被顶到前面、排序字段走白名单不接受任意输入。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.daily_data_api import list_ad_daily, list_qianniu_daily
from app.modules.collect.models import AdDaily, QianniuDaily

# QianniuDaily / AdDaily 都有指向 platform_product 的外键；只导入 collect.models
# 会让该表缺席 metadata，schema 准备阶段直接抛 NoReferencedTableError。
from app.modules.product.platform_product_models import PlatformProduct  # noqa: F401


async def _seed_qianniu(session: AsyncSession, tenant: Any) -> None:
    rows = [
        # platform_id, date, visitors, pay_amount, pay_orders
        ("P100", date(2026, 9, 1), 10, Decimal("100.00"), 1),
        ("P200", date(2026, 9, 2), 50, Decimal("500.00"), 5),
        ("P300", date(2026, 9, 3), 90, Decimal("900.00"), 9),
        # 金额为空：排序时不能被顶到降序最前面
        ("P400", date(2026, 9, 4), None, None, None),
    ]
    for platform_id, day, visitors, pay_amount, pay_orders in rows:
        session.add(
            QianniuDaily(
                tenant_id=tenant.id,
                platform_id_snapshot=platform_id,
                date=day,
                visitors=visitors,
                pay_amount=pay_amount,
                pay_orders=pay_orders,
                extra={"退款金额": "0"},
            )
        )
    await session.flush()


async def _seed_ad(session: AsyncSession, tenant: Any) -> None:
    rows = [
        ("A100", date(2026, 9, 1), Decimal("10.00"), 1000, 10, Decimal("200.00")),
        ("A200", date(2026, 9, 2), Decimal("80.00"), 8000, 80, Decimal("900.00")),
    ]
    for platform_id, day, cost, impressions, clicks, gmv in rows:
        session.add(
            AdDaily(
                tenant_id=tenant.id,
                platform_id_snapshot=platform_id,
                date=day,
                cost=cost,
                impressions=impressions,
                clicks=clicks,
                gmv=gmv,
                extra={},
            )
        )
    await session.flush()


def _ids(result: dict[str, Any]) -> list[str]:
    return [item["platform_id"] for item in result["items"]]


@pytest.mark.integration
@pytest.mark.asyncio
class TestQianniuDailyFilters:
    async def test_date_range_is_inclusive(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(
                session,
                user,
                date_from=date(2026, 9, 2),
                date_to=date(2026, 9, 3),
            )
            assert sorted(_ids(result)) == ["P200", "P300"]
            assert result["total"] == 2
        finally:
            tenant_id_ctx.reset(token)

    async def test_platform_id_is_partial_match(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(session, user, platform_id="20")
            assert _ids(result) == ["P200"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_numeric_range_is_inclusive(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(
                session,
                user,
                pay_amount_min=Decimal("100.00"),
                pay_amount_max=Decimal("500.00"),
            )
            assert sorted(_ids(result)) == ["P100", "P200"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_combined_filters_narrow_further(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(
                session,
                user,
                date_from=date(2026, 9, 1),
                date_to=date(2026, 9, 3),
                visitors_min=50,
                pay_orders_max=5,
            )
            assert _ids(result) == ["P200"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_sort_ascending_by_pay_amount(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(session, user, sort_by="pay_amount", sort_dir="asc")
            # NULL 排最后，其余升序
            assert _ids(result) == ["P100", "P200", "P300", "P400"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_sort_descending_keeps_nulls_last(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        """降序时空值也排最后 —— 按金额从高到低看，空值顶在最前没有意义。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(session, user, sort_by="pay_amount", sort_dir="desc")
            assert _ids(result) == ["P300", "P200", "P100", "P400"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_unknown_sort_field_falls_back_to_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        """排序字段走白名单：未知字段回落默认列，不把请求字符串拼进 SQL。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(
                session, user, sort_by="pay_amount; DROP TABLE style", sort_dir="desc"
            )
            assert _ids(result) == ["P400", "P300", "P200", "P100"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_pagination_reports_unfiltered_total_of_filtered_set(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        """total 是筛选后的总数，不是当前页条数。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_qianniu(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_qianniu_daily(session, user, page=1, page_size=2)
            assert len(result["items"]) == 2
            assert result["total"] == 4
            assert result["page"] == 1
            assert result["page_size"] == 2
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestAdDailyFilters:
    async def test_cost_range_and_sort(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_ad(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])

            filtered = await list_ad_daily(session, user, cost_min=Decimal("50.00"))
            assert _ids(filtered) == ["A200"]

            ordered = await list_ad_daily(session, user, sort_by="clicks", sort_dir="asc")
            assert _ids(ordered) == ["A100", "A200"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_impressions_and_gmv_ranges(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_ad(session, tenant_a)
            user = await factory.user(tenant_a, roles=[admin_role])
            result = await list_ad_daily(
                session,
                user,
                impressions_min=500,
                impressions_max=5000,
                gmv_max=Decimal("300.00"),
            )
            assert _ids(result) == ["A100"]
        finally:
            tenant_id_ctx.reset(token)
