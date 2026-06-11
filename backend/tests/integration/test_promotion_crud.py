"""U04 Promotion CRUD 集成测试（EP05-S02 / S03 / S04 / S05）。

覆盖：
- 创建推广 + 自动 internal_code 生成
- 字段快照（style_code / style_short_name / quote_amount）
- 重复检测（EP05-S04 warning，非阻塞）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.exceptions import (
    InvalidBloggerReferenceError,
    InvalidStyleReferenceError,
    PromotionNotFoundError,
)
from app.modules.promotion.schemas import (
    PromotionCreate,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreatePromotion:
    async def test_create_basic_with_blogger_quote_snapshot(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """EP05-S02: 创建推广，blogger.quote 自动快照为 quote_amount."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(short_name="测试简称")
            blogger = await blogger_factory.blogger(quote=Decimal("888.00"))
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert response.style_code_snapshot == style.style_code
            assert response.style_short_name_snapshot == "测试简称"
            assert response.quote_amount == Decimal("888.00")
            assert response.publish_status == "未发布"
            assert response.recall_status == "未召回"
            assert response.settlement_status == "未核查"
            assert response.is_active is True
            # internal_code 格式：<前缀><yyMMdd><0001>
            assert response.internal_code.endswith("0001")
            assert "260526" in response.internal_code
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_short_name_fallback(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """EP05-S03: short_name 为 None 时 fallback 用 style_name."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(
                style_name="完整款名", short_name=None
            )
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert response.style_short_name_snapshot == "完整款名"
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_explicit_quote_amount_override(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """PR 显式传 quote_amount 优先于 blogger.quote."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                    quote_amount=Decimal("999.00"),
                ),
                user,
            )
            assert response.quote_amount == Decimal("999.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_nonexistent_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)
            with pytest.raises(InvalidStyleReferenceError):
                await svc.create_promotion(
                    PromotionCreate(
                        style_id=uuid4(),
                        blogger_id=blogger.id,
                        platform="小红书",
                        cooperation_date=date(2026, 5, 26),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_nonexistent_blogger(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            svc = PromotionService(session)
            with pytest.raises(InvalidBloggerReferenceError):
                await svc.create_promotion(
                    PromotionCreate(
                        style_id=style.id,
                        blogger_id=uuid4(),
                        platform="小红书",
                        cooperation_date=date(2026, 5, 26),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDuplicateWarning:
    async def test_duplicate_returns_warning_not_block(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """EP05-S04: 同款 + 同博主再创建返回 warnings 而非阻塞."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()

            # 已存在 1 条活跃推广
            existing = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user
            )

            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 27),
                ),
                user,
            )
            # 不阻塞：返回新的 promotion + duplicate_warnings 含已存在的
            assert response.id != existing.id
            assert len(response.duplicate_warnings) == 1
            assert response.duplicate_warnings[0].promotion_id == existing.id
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSequenceGeneration:
    async def test_sequence_increments_per_day(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """同租户同 cooperation_date 序号递增 0001 → 0002."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)

            r1 = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            r2 = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="抖音",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert r1.internal_code.endswith("0001")
            assert r2.internal_code.endswith("0002")
        finally:
            tenant_id_ctx.reset(token)

    async def test_sequence_resets_per_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)

            r1 = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            r2 = await svc.create_promotion(
                PromotionCreate(
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 27),
                ),
                user,
            )
            # 不同日期独立计数
            assert r1.internal_code.endswith("0001")
            assert r2.internal_code.endswith("0001")


        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdatePromotion:
    async def test_update_quote_amount(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user,
                quote_amount=Decimal("100.00"),
            )
            svc = PromotionService(session)
            response = await svc.update_promotion(
                promotion.id,
                PromotionUpdate(quote_amount=Decimal("300.00")),
                user,
            )
            assert response.quote_amount == Decimal("300.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_nonexistent(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PromotionService(session)
            with pytest.raises(PromotionNotFoundError):
                await svc.update_promotion(
                    uuid4(),
                    PromotionUpdate(remark="x"),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)
