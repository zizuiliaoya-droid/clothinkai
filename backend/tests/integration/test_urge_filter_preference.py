"""催发页的筛选记忆（流程线 PR-5 / 8.7）：page_code = urge_tasks 经接口函数读写成功。

白名单 ``FILTER_PAGE_CODES`` 不收这个编码的话，GET 422、PUT 422，前端 useFilterMemory 静默失效。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.report.preference_api import (
    get_filter_preference,
    save_filter_preference,
)
from app.modules.report.user_preference_service import UserPreferenceService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_urge_tasks_filter_save_then_read(
    session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
) -> None:
    tok = tenant_id_ctx.set(tenant_a.id)
    try:
        user = await factory.user(tenant_a, roles=[pr_role])
        svc = UserPreferenceService(session)
        # 没存过 → 空字典（不是 422）
        assert await get_filter_preference("urge_tasks", user, svc) == {}
        payload = {"style_id": "s-1", "style_label": "ST-001 飞狐短裙", "mine": True}
        assert await save_filter_preference("urge_tasks", user, svc, payload) == {"ok": True}
        assert await get_filter_preference("urge_tasks", user, svc) == payload
        # 关掉「只看我的」、清掉款式 → 整块覆盖
        cleared = {"style_id": None, "style_label": None, "mine": False}
        await save_filter_preference("urge_tasks", user, svc, cleared)
        assert await get_filter_preference("urge_tasks", user, svc) == cleared
    finally:
        tenant_id_ctx.reset(tok)
