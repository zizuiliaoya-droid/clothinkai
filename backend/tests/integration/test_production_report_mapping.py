"""投产报表的「千牛日报 → 商品」映射口径。

这组测试是为了一次**性能重构**立的护栏：映射逻辑原来内联在
``LEFT JOIN qianniu_daily ON (...)`` 的条件里，PostgreSQL 对每个 (商品, 日报) 组合
都要跑一遍里面的 EXISTS / NOT EXISTS / COUNT(DISTINCT)。生产实测 264 商品 × 310 行
日报 = 81,840 次子查询，整条 SQL 1848ms —— 处理 310 行数据。

重构把映射提成 ``qn_link`` CTE（只算一次）。口径必须**逐行不变**，所以这里把四种
映射情形都钉住：

1. 正路：platform_product 建了千牛链接并绑了商品
2. 兜底：链接没建，款式上手填了千牛ID，且该 ID 只落在一个商品
3. 兜底被护栏挡掉：同一千牛ID 落在多个商品 → 不归任何商品（宁可漏不可重复计）
4. 套装：多个款式共用一条链接 → 合并一行，销售额只算一次

生产数据背景：``style.qianniu_product_id`` 非空只有 2 条、走兜底的日报只有 20 行，
这套三层嵌套子查询却占掉整条查询 67% 的时间。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.report.advanced_repository import ProductionRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

D1 = date(2026, 3, 1)
LO, HI = date(2026, 1, 1), date(2026, 12, 31)


async def _goods(session: AsyncSession, tenant_id: Any, *, code: str, is_suit: bool = False) -> Any:
    gid = uuid4()
    await session.execute(
        sa_text(
            "INSERT INTO goods_main (id, tenant_id, goods_code, goods_title, is_suit, "
            "is_active, is_deleted, created_at, updated_at) "
            "VALUES (:id, :t, :code, :title, :suit, true, false, NOW(), NOW())"
        ),
        {"id": gid, "t": tenant_id, "code": code, "title": f"{code} 商品", "suit": is_suit},
    )
    return gid


async def _link_style(
    session: AsyncSession, tenant_id: Any, goods_id: Any, style_id: Any, *, order: int = 0
) -> None:
    await session.execute(
        sa_text(
            "INSERT INTO goods_style_item (id, tenant_id, goods_main_id, style_id, "
            "sort_order, is_active, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :t, :g, :s, :o, true, NOW(), NOW())"
        ),
        {"t": tenant_id, "g": goods_id, "s": style_id, "o": order},
    )


async def _platform_product(
    session: AsyncSession,
    tenant_id: Any,
    goods_id: Any,
    platform_id: str,
    *,
    style_id: Any,
) -> Any:
    """建一条千牛平台链接。

    ``style_id`` 必传：这张表的 style_id 是 NOT NULL（1a 的 goods_main_id 是加在
    原有 style_id 之上的，不是替代它）。报表的映射只看 goods_main_id，
    但建行绕不开 style_id。
    """
    pid = uuid4()
    await session.execute(
        sa_text(
            "INSERT INTO platform_product (id, tenant_id, platform, platform_id, "
            "style_id, goods_main_id, is_active, created_at, updated_at) "
            "VALUES (:id, :t, '千牛', :pid, :s, :g, true, NOW(), NOW())"
        ),
        {"id": pid, "t": tenant_id, "pid": platform_id, "s": style_id, "g": goods_id},
    )
    return pid


async def _qianniu(
    session: AsyncSession,
    tenant_id: Any,
    *,
    platform_id: str,
    pay: Decimal,
    platform_product_id: Any = None,
    day: date = D1,
    extra: str = "{}",
    refund: Decimal | None = None,
    add_cart: int | None = None,
) -> None:
    await session.execute(
        sa_text(
            "INSERT INTO qianniu_daily (id, tenant_id, platform_product_id, "
            "platform_id_snapshot, date, visitors, pay_amount, pay_orders, "
            "refund_amount, add_cart_count, extra, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :t, :ppid, :pid, :d, 10, :pay, 1, :refund, :cart, "
            "CAST(:extra AS JSONB), NOW(), NOW())"
        ),
        {
            "t": tenant_id,
            "ppid": platform_product_id,
            "pid": platform_id,
            "d": day,
            "pay": pay,
            "refund": refund,
            "cart": add_cart,
            "extra": extra,
        },
    )


async def _rows(session: AsyncSession, tenant_id: Any) -> dict[str, Any]:
    out = await ProductionRepository(session).aggregate_by_goods(
        tenant_id=tenant_id, date_from=LO, date_to=HI, exclude_brushing=False
    )
    return {r["goods_code"]: r for r in out}


class TestMappingBranches:
    async def test_mapped_via_platform_product(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """正路：建了千牛链接并绑了商品。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="MAP_OK")
            g = await _goods(session, tenant_a.id, code="G_MAP_OK")
            await _link_style(session, tenant_a.id, g, style.id)
            pp = await _platform_product(session, tenant_a.id, g, "QN_MAP_OK", style_id=style.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_MAP_OK",
                pay=Decimal("1000.00"),
                platform_product_id=pp,
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            assert rows["G_MAP_OK"]["pay_amount"] == Decimal("1000.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_mapped_by_platform_id_when_fk_null(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """链接建了但日报没回填 FK —— 靠 platform_id 字符串对上。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="MAP_SNAP")
            g = await _goods(session, tenant_a.id, code="G_MAP_SNAP")
            await _link_style(session, tenant_a.id, g, style.id)
            await _platform_product(session, tenant_a.id, g, "QN_SNAP", style_id=style.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_SNAP",
                pay=Decimal("500.00"),
                platform_product_id=None,
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            assert rows["G_MAP_SNAP"]["pay_amount"] == Decimal("500.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_legacy_fallback_single_goods(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """兜底：没建链接，款式上手填了千牛ID，且只落在一个商品。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(
                style_code="LEG_ONE", qianniu_product_id="QN_LEGACY_ONE"
            )
            g = await _goods(session, tenant_a.id, code="G_LEG_ONE")
            await _link_style(session, tenant_a.id, g, style.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_LEGACY_ONE",
                pay=Decimal("777.00"),
                platform_product_id=None,
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            assert rows["G_LEG_ONE"]["pay_amount"] == Decimal("777.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_legacy_fallback_blocked_when_ambiguous(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """同一千牛ID 落在两个商品 → 不归任何一个。

        护栏的意义：宁可这条日报不进报表，也不能让两个商品各算一次把总额翻倍。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            s1 = await product_factory.style(
                style_code="LEG_AMB_1", qianniu_product_id="QN_LEGACY_AMB"
            )
            s2 = await product_factory.style(
                style_code="LEG_AMB_2", qianniu_product_id="QN_LEGACY_AMB"
            )
            g1 = await _goods(session, tenant_a.id, code="G_LEG_AMB_1")
            g2 = await _goods(session, tenant_a.id, code="G_LEG_AMB_2")
            await _link_style(session, tenant_a.id, g1, s1.id)
            await _link_style(session, tenant_a.id, g2, s2.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_LEGACY_AMB",
                pay=Decimal("999.00"),
                platform_product_id=None,
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            # 两个商品都三项全 0，被 HAVING 过滤掉，压根不出现在结果里
            assert "G_LEG_AMB_1" not in rows
            assert "G_LEG_AMB_2" not in rows
        finally:
            tenant_id_ctx.reset(token)

    async def test_suit_shares_one_link_counted_once(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """套装两个款式共用一条链接 → 合并一行，销售额只算一次。

        这是 1b 当初把报表从款式维度切到商品维度要解决的核心问题：
        按款式算会让套装的销售额翻倍。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            s1 = await product_factory.style(style_code="SUIT_A")
            s2 = await product_factory.style(style_code="SUIT_B")
            g = await _goods(session, tenant_a.id, code="G_SUIT", is_suit=True)
            await _link_style(session, tenant_a.id, g, s1.id, order=0)
            await _link_style(session, tenant_a.id, g, s2.id, order=1)
            pp = await _platform_product(session, tenant_a.id, g, "QN_SUIT", style_id=s1.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_SUIT",
                pay=Decimal("2000.00"),
                platform_product_id=pp,
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            row = rows["G_SUIT"]
            assert row["pay_amount"] == Decimal("2000.00")
            assert row["is_suit"] is True
            assert sorted((row["style_codes"] or "").split(",")) == ["SUIT_A", "SUIT_B"]
        finally:
            tenant_id_ctx.reset(token)

    async def test_one_report_row_per_goods_no_duplication(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """同一商品多天日报累加，但只出一行。

        CTE 用的是 ``UNION`` 而不是 ``UNION ALL``：如果某条日报同时命中正路与兜底
        （理论上互斥，但数据脏时可能同时满足），UNION 去重保证它不会被算两次。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="MULTI_DAY")
            g = await _goods(session, tenant_a.id, code="G_MULTI_DAY")
            await _link_style(session, tenant_a.id, g, style.id)
            pp = await _platform_product(session, tenant_a.id, g, "QN_MULTI", style_id=style.id)
            for i, amt in enumerate(("100.00", "200.00", "300.00")):
                await _qianniu(
                    session,
                    tenant_a.id,
                    platform_id="QN_MULTI",
                    pay=Decimal(amt),
                    platform_product_id=pp,
                    day=date(2026, 3, 1 + i),
                )
            await session.flush()

            out = await ProductionRepository(session).aggregate_by_goods(
                tenant_id=tenant_a.id, date_from=LO, date_to=HI, exclude_brushing=False
            )
            matched = [r for r in out if r["goods_code"] == "G_MULTI_DAY"]
            assert len(matched) == 1
            assert matched[0]["pay_amount"] == Decimal("600.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_refund_and_add_cart_come_from_typed_columns(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """退款额与加购数读 typed 列（055），不再从 extra 按键抠。

        extra 里故意放一组英文键的诱饵值：以前报表就是按这两个键读的，而真实导入从来
        不写它们 —— 生产上因此一直是 0。读回诱饵值说明又退回了按键读 extra。
        typed 列为 NULL（导出里是 "-" 或没有这一列）的那天不计入，也不能让查询报错。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="EXTRA_OK")
            g = await _goods(session, tenant_a.id, code="G_EXTRA")
            await _link_style(session, tenant_a.id, g, style.id)
            pp = await _platform_product(session, tenant_a.id, g, "QN_EXTRA", style_id=style.id)
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_EXTRA",
                pay=Decimal("1000.00"),
                platform_product_id=pp,
                day=date(2026, 3, 1),
                refund=Decimal("120.50"),
                add_cart=7,
                extra=(
                    '{"成功退款金额": "120.50", "商品加购件数": "7", '
                    '"refund_amount": "999", "add_cart_count": "999"}'
                ),
            )
            await _qianniu(
                session,
                tenant_a.id,
                platform_id="QN_EXTRA",
                pay=Decimal("500.00"),
                platform_product_id=pp,
                day=date(2026, 3, 2),
                extra='{"成功退款金额": "-", "商品加购件数": "-"}',
            )
            await session.flush()

            rows = await _rows(session, tenant_a.id)
            row = rows["G_EXTRA"]
            assert row["pay_amount"] == Decimal("1500.00")
            assert row["refund_amount"] == Decimal("120.50")
            assert int(row["add_cart_count"]) == 7
        finally:
            tenant_id_ctx.reset(token)
