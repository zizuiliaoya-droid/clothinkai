"""流程线 PR-5 S3：PATCH 的五个分组入矩阵（设计 5.4「入矩阵」一列、9.3 G 块字段写权限）。

归属商品（``goods``）、合作信息（``cooperation``：平台、约定发布时间、备注）、合作模式（``cooperation_mode``）、
服务费（``quote_amount``）、停用（``is_active`` → 动作 ``deactivate``）从这个 PR 起按矩阵当前格判：
PR 一律「读」（11-16、11-53），越权的一次全列出来 403 ``FIELD_PERMISSION_DENIED``；没入矩阵的（``note_title``、
``source_extra``）照旧可写。合作模式「有值改成另一个值」先于矩阵 409（²³）。角色用 ``flow_users``（迁移 seed 的真实角色）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import CooperationModeImmutableError, FieldPermissionDenied
from app.modules.promotion.schemas import PromotionUpdate
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _promotion(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any, **kw: Any
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("internal_code", f"DEPG{uuid4().hex[:8].upper()}")
    return await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr, **kw)


async def _row(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            sa_text(
                "SELECT platform, remark, quote_amount, cooperation_mode, is_active, note_title "
                "FROM promotion WHERE id = :pid"
            ),
            {"pid": promotion_id},
        )
    ).mappings()
    return dict(row.one())


@pytest.mark.usefixtures("tenant_ctx")
class TestPatchGroupsPr5:
    async def test_pr_denied_lists_all_fields_in_order(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PR PATCH 服务费 + 平台 + 归属商品 + 停用 → 403，``fields`` 按 patch_groups 登记顺序全列，库不变。"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待发货"
        )
        before = await _row(session, promo.id)
        svc = PromotionService(session)
        with pytest.raises(FieldPermissionDenied) as exc:
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(
                    quote_amount=Decimal("800"),
                    platform="抖音",
                    goods_main_id=uuid4(),
                    is_active=False,
                    note_title="标题",
                ),
                flow_users.pr,
            )
        assert exc.value.code == "FIELD_PERMISSION_DENIED"
        assert exc.value.status_code == 403
        assert exc.value.details["fields"] == [
            "goods_main_id",
            "platform",
            "quote_amount",
            "is_active",
        ]
        assert await _row(session, promo.id) == before

    @pytest.mark.parametrize(
        "patch",
        [
            {"remark": "改备注"},
            {"scheduled_publish_date": "2026-11-01"},
            {"cooperation_mode": "寄拍"},  # 空模式补一次：PR 也不行（11-16）
            {"platform": "抖音"},
            {"quote_amount": "600"},
            {"is_active": False},
        ],
    )
    async def test_pr_each_group_denied(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        patch: dict[str, Any],
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        with pytest.raises(FieldPermissionDenied) as exc:
            await PromotionService(session).update_promotion(
                promo.id, PromotionUpdate.model_validate(patch), flow_users.pr
            )
        assert exc.value.details["fields"] == list(patch)

    async def test_pr_unmanaged_fields_still_writable(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """note_title、source_extra 没入矩阵（随 PR-10），PR 照旧能改；合作模式传 null 是空操作，不判权。"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, cooperation_mode="寄拍"
        )
        resp = await PromotionService(session).update_promotion(
            promo.id,
            PromotionUpdate(
                note_title="新标题", source_extra={"订单号": "A001"}, cooperation_mode=None
            ),
            flow_users.pr,
        )
        assert resp.note_title == "新标题"
        row = await _row(session, promo.id)
        assert row["note_title"] == "新标题"
        assert row["cooperation_mode"] == "寄拍"

    async def test_manager_edits_cooperation_before_settlement(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """主管在 A ~ D 列能改合作信息、服务费、补空模式（场景有效性：同一组字段 PR 403、主管 200）。"""
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        await PromotionService(session).update_promotion(
            promo.id,
            PromotionUpdate(
                platform="抖音",
                remark="主管改",
                quote_amount=Decimal("700"),
                cooperation_mode=CooperationMode.CONSIGNMENT,
            ),
            flow_users.pr_manager,
        )
        row = await _row(session, promo.id)
        assert row["platform"] == "抖音"
        assert row["remark"] == "主管改"
        assert row["quote_amount"] == Decimal("700.00")
        assert row["cooperation_mode"] == CooperationMode.CONSIGNMENT.value

    async def test_quote_read_only_after_settlement_created(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """E 列（待财务付款）起服务费所有人只读，包括管理员；合作信息只有管理员改。"""
        promo = await _promotion(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待付款",
        )
        svc = PromotionService(session)
        for who in (flow_users.pr_manager, flow_users.admin):
            with pytest.raises(FieldPermissionDenied) as exc:
                await svc.update_promotion(
                    promo.id, PromotionUpdate(quote_amount=Decimal("900")), who
                )
            assert exc.value.details["fields"] == ["quote_amount"]
        with pytest.raises(FieldPermissionDenied) as exc:
            await svc.update_promotion(promo.id, PromotionUpdate(remark="x"), flow_users.pr_manager)
        assert exc.value.details["fields"] == ["remark"]
        await svc.update_promotion(promo.id, PromotionUpdate(remark="管理员改"), flow_users.admin)
        row = await _row(session, promo.id)
        assert row["remark"] == "管理员改"
        assert row["quote_amount"] == Decimal("500.00")

    async def test_mode_immutable_before_matrix(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """有值改成另一个值 → 409（PR 也是 409、不是 403）；同值是空操作 200（主管、PR 都是）。"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, cooperation_mode="寄拍"
        )
        svc = PromotionService(session)
        for who in (flow_users.pr_manager, flow_users.pr):
            with pytest.raises(CooperationModeImmutableError) as exc:
                await svc.update_promotion(
                    promo.id, PromotionUpdate(cooperation_mode=CooperationMode.BARTER), who
                )
            assert exc.value.code == "COOPERATION_MODE_IMMUTABLE"
            assert exc.value.status_code == 409
            await svc.update_promotion(
                promo.id, PromotionUpdate(cooperation_mode=CooperationMode.CONSIGNMENT), who
            )
        assert (await _row(session, promo.id))["cooperation_mode"] == "寄拍"
