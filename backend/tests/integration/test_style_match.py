"""U02 款号 ↔ 商品简称双向关联集成测试（EP02-S06 + 降级语义 FB1）.

覆盖：
- 精确匹配 / 模糊匹配
- 业务未匹配返回 200 + 空候选（前端继续输入）
- 系统失败让异常自然冒泡（5xx + Sentry，不返回空候选）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.service import StyleService


@pytest.mark.integration
@pytest.mark.asyncio
class TestExactMatch:
    async def test_match_by_code_found(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(
                style_code="W001",
                style_name="波点花边连衣裙",
                short_name="波点花边",
            )
            svc = StyleService(session)
            response = await svc.match_by_code("W001")
            assert response.matched is True
            assert response.total == 1
            assert response.candidates[0].style_code == "W001"
            assert response.candidates[0].display_short_name == "波点花边"
        finally:
            tenant_id_ctx.reset(token)

    async def test_match_by_code_not_found_returns_empty(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        """业务未匹配 → 200 + 空候选（前端允许继续输入）."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = StyleService(session)
            response = await svc.match_by_code("UNKNOWN")
            assert response.matched is False
            assert response.candidates == []
            assert response.total == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_match_by_code_inactive_excluded(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """停用的 style 不会被 match 命中."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(
                style_code="W002", is_active=False
            )
            svc = StyleService(session)
            response = await svc.match_by_code("W002")
            assert response.matched is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_display_short_name_falls_back(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """short_name 为 NULL 时 display_short_name 回退到 style_name."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(
                style_code="W003",
                style_name="无简称款式",
                short_name=None,
            )
            svc = StyleService(session)
            response = await svc.match_by_code("W003")
            assert response.candidates[0].display_short_name == "无简称款式"
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestKeywordMatch:
    async def test_match_keyword_returns_candidates(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(
                style_code="W001",
                style_name="波点花边连衣裙",
                short_name="波点花边",
            )
            await product_factory.style(
                style_code="W002",
                style_name="纯色 T 恤",
                short_name=None,
            )
            svc = StyleService(session)
            response = await svc.match_by_keyword("波点")
            assert response.matched is True
            assert response.total >= 1
            codes = {c.style_code for c in response.candidates}
            assert "W001" in codes
            assert "W002" not in codes
        finally:
            tenant_id_ctx.reset(token)

    async def test_match_keyword_empty_no_match(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """业务未匹配关键字 → 200 + 空候选."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(style_code="W001", style_name="花裙")
            svc = StyleService(session)
            response = await svc.match_by_keyword("不存在的关键字")
            assert response.matched is False
            assert response.candidates == []
            assert response.total == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_match_keyword_blank_returns_empty(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = StyleService(session)
            response = await svc.match_by_keyword("   ")
            assert response.matched is False
            assert response.candidates == []
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSystemFailureNotMaskedAsEmpty:
    """FB1 关键测试：DB 异常 / 系统失败必须让异常冒泡，不能伪装成空候选."""

    async def test_db_error_propagates(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = StyleService(session)
            # mock repository 层抛系统异常
            from app.modules.product.repository import StyleRepository

            with patch.object(
                StyleRepository,
                "search_by_keyword",
                new=AsyncMock(side_effect=RuntimeError("simulated DB outage")),
            ), pytest.raises(RuntimeError, match="simulated DB outage"):
                await svc.match_by_keyword("anything")
        finally:
            tenant_id_ctx.reset(token)

    async def test_db_error_in_exact_match_propagates(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = StyleService(session)
            from app.modules.product.repository import StyleRepository

            with patch.object(
                StyleRepository,
                "get_by_code",
                new=AsyncMock(side_effect=RuntimeError("simulated DB outage")),
            ), pytest.raises(RuntimeError, match="simulated DB outage"):
                await svc.match_by_code("W001")
        finally:
            tenant_id_ctx.reset(token)
