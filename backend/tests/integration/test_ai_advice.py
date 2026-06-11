"""U18 集成测试：AI 建议（monkeypatch DeepSeek）成功/降级 + 数据不足 + 404 + RLS。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.core.tenancy import tenant_id_ctx
from app.modules.ai.client import DeepSeekClient
from app.modules.ai.exceptions import (
    AiDataInsufficientError,
    AiServiceUnavailableError,
)
from app.modules.ai.models import AiAdviceLog
from app.modules.ai.service import AiAdvisoryService

pytestmark = pytest.mark.asyncio


async def _ok_chat(self, messages, *, model=None):  # noqa: ANN001
    return {"content": "建议扩大投放", "model": "deepseek-chat", "latency_ms": 12}


async def _fail_chat(self, messages, *, model=None):  # noqa: ANN001
    raise AiServiceUnavailableError()


class TestStrategy:
    async def test_data_insufficient(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        pr_manager_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            from datetime import date
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            # 无推广数据 → months=0 < 6 → 422，不调 AI
            with pytest.raises(AiDataInsufficientError):
                await AiAdvisoryService(session).strategy_advice(
                    (date(2026, 1, 1), date(2026, 6, 30)), user
                )
        finally:
            tenant_id_ctx.reset(tok)


class TestBloggerSuggest:
    async def test_success_logs_and_returns(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any,
        product_factory: Any, blogger_factory: Any, monkeypatch: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            monkeypatch.setattr(DeepSeekClient, "chat", _ok_chat)
            user = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            await blogger_factory.blogger(nickname="博主A", follower_count=1000)
            await blogger_factory.blogger(nickname="博主B", follower_count=500)
            await session.commit()

            out = await AiAdvisoryService(session).blogger_suggest(
                style.id, 2, user
            )
            assert len(out) == 2
            assert out[0].match_score >= out[1].match_score
            cnt = (await session.execute(
                select(func.count()).select_from(AiAdviceLog).where(
                    AiAdviceLog.tenant_id == tenant_a.id,
                    AiAdviceLog.status == "success",
                )
            )).scalar_one()
            assert cnt == 1
        finally:
            tenant_id_ctx.reset(tok)

    async def test_degraded_logs_and_raises(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any,
        product_factory: Any, blogger_factory: Any, monkeypatch: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            monkeypatch.setattr(DeepSeekClient, "chat", _fail_chat)
            user = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            await blogger_factory.blogger(nickname="博主C")
            await session.commit()

            with pytest.raises(AiServiceUnavailableError):
                await AiAdvisoryService(session).blogger_suggest(style.id, 5, user)
            cnt = (await session.execute(
                select(func.count()).select_from(AiAdviceLog).where(
                    AiAdviceLog.tenant_id == tenant_a.id,
                    AiAdviceLog.status == "degraded",
                )
            )).scalar_one()
            assert cnt == 1
        finally:
            tenant_id_ctx.reset(tok)

    async def test_style_not_found(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any,
    ) -> None:
        from uuid import uuid4

        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            with pytest.raises(ResourceNotFoundError):
                await AiAdvisoryService(session).blogger_suggest(uuid4(), 5, user)
        finally:
            tenant_id_ctx.reset(tok)


class TestAnomaly:
    async def test_alert_not_found(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        operations_role: Any,
    ) -> None:
        from uuid import uuid4

        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[operations_role])
            with pytest.raises(ResourceNotFoundError):
                await AiAdvisoryService(session).anomaly_diagnosis(uuid4(), user)
        finally:
            tenant_id_ctx.reset(tok)
