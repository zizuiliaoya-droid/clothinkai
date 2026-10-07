"""「录入信息」保存按键合并（7a-5，PATCH /api/promotions/{id} 的 source_extra）。

以前是整包覆盖：前端把打开弹窗时的快照连同表单值一起发回来，表单上没有的键被删，
弹窗开着期间仓库回填的发货单号也会被旧快照冲掉。现在补丁里没出现的键不动，
null 或空白只删那一个键。断言一律直接 SELECT 库里的 source_extra，不看响应。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog
from app.modules.promotion.schemas import (
    PromotionListFilters,
    PromotionUpdate,
    PromotionWarehouseWaybillRequest,
)
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _db_extra(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    value = (
        await session.execute(
            sa_text("SELECT source_extra FROM promotion WHERE id = :pid"), {"pid": promotion_id}
        )
    ).scalar_one()
    return dict(value)


async def _db_updated_at(session: AsyncSession, promotion_id: UUID) -> Any:
    return (
        await session.execute(
            sa_text("SELECT updated_at FROM promotion WHERE id = :pid"), {"pid": promotion_id}
        )
    ).scalar_one()


async def _audit_count(session: AsyncSession, promotion_id: UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.resource_id == str(promotion_id))
            )
        ).scalar_one()
    )


async def _seed(
    *,
    factory: Any,
    tenant_a: Any,
    pr_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    source_extra: dict[str, Any],
) -> tuple[Any, UUID]:
    pr = await factory.user(tenant_a, roles=[pr_role])
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style, blogger=blogger, pr=pr, source_extra=source_extra
    )
    return pr, promo.id


class TestSourceExtraMerge:
    async def test_keys_outside_patch_survive(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """① 旧字段（寄回单号 / 点赞数）与导入写的「博主风格」不在表单上，保存后原样还在。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            legacy = {"寄回单号": "SF0001", "点赞数": 120, "博主风格": "甜美"}
            pr, pid = await _seed(
                factory=factory,
                tenant_a=tenant_a,
                pr_role=pr_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
                source_extra=legacy,
            )
            resp = await PromotionService(session).update_promotion(
                pid, PromotionUpdate(source_extra={"订单号": "TB001"}), pr
            )
            expected = {**legacy, "订单号": "TB001"}
            assert await _db_extra(session, pid) == expected
            assert resp.source_extra == expected
        finally:
            tenant_id_ctx.reset(token)

    async def test_stale_snapshot_does_not_wipe_warehouse_waybill(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """② PR 打开录入信息时还没有发货单号；仓库此时回填 SF123；PR 只改订单号保存 → SF123 仍在。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, pid = await _seed(
                factory=factory,
                tenant_a=tenant_a,
                pr_role=pr_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
                source_extra={"打单地址": "浙江省杭州市某路 1 号"},
            )
            svc = PromotionService(session)
            # PR 打开弹窗时的快照（列表接口给的）：没有发货单号
            page = await svc.list_promotions(
                filters=PromotionListFilters(), page=1, page_size=20, user=pr
            )
            snapshot = next(p.source_extra for p in page.items if p.id == pid)
            assert "发货单号" not in snapshot

            warehouse = await factory.user(tenant_a, roles=[admin_role])
            await svc.update_warehouse_waybill(
                pid, PromotionWarehouseWaybillRequest(waybill="SF123"), warehouse
            )

            # 前端只提交相对快照改过的键
            await svc.update_promotion(pid, PromotionUpdate(source_extra={"订单号": "TB001"}), pr)

            assert await _db_extra(session, pid) == {
                "打单地址": "浙江省杭州市某路 1 号",
                "发货单号": "SF123",
                "订单号": "TB001",
            }
        finally:
            tenant_id_ctx.reset(token)

    async def test_null_or_blank_deletes_only_that_key(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """③ null 与全空白各只删那一个键。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, pid = await _seed(
                factory=factory,
                tenant_a=tenant_a,
                pr_role=pr_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
                source_extra={"订单号": "TB001", "负责PR": "小王", "博主风格": "甜美"},
            )
            svc = PromotionService(session)

            await svc.update_promotion(pid, PromotionUpdate(source_extra={"订单号": None}), pr)
            assert await _db_extra(session, pid) == {"负责PR": "小王", "博主风格": "甜美"}

            await svc.update_promotion(pid, PromotionUpdate(source_extra={"负责PR": "   "}), pr)
            assert await _db_extra(session, pid) == {"博主风格": "甜美"}
        finally:
            tenant_id_ctx.reset(token)

    async def test_patch_equal_to_current_writes_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """④ 补丁合并后与现值相同 → 不写库（updated_at 不变）、不写 audit。

        最后补一次真改动做对照：updated_at 会变，证明前面「不变」不是因为测不出来。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, pid = await _seed(
                factory=factory,
                tenant_a=tenant_a,
                pr_role=pr_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
                source_extra={"订单号": "TB001", "博主风格": "甜美"},
            )
            svc = PromotionService(session)
            before_at = await _db_updated_at(session, pid)
            before_audits = await _audit_count(session, pid)

            for patch in ({"订单号": "TB001"}, {"订单号": "  TB001  "}, {"负责PR": None}):
                await svc.update_promotion(pid, PromotionUpdate(source_extra=patch), pr)

            assert await _db_extra(session, pid) == {"订单号": "TB001", "博主风格": "甜美"}
            assert await _db_updated_at(session, pid) == before_at
            assert await _audit_count(session, pid) == before_audits

            # 对照：真改了 updated_at 会动
            await svc.update_promotion(pid, PromotionUpdate(source_extra={"订单号": "TB002"}), pr)
            assert await _db_updated_at(session, pid) > before_at
            assert (await _db_extra(session, pid))["订单号"] == "TB002"
        finally:
            tenant_id_ctx.reset(token)
