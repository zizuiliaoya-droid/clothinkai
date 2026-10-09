"""8b：灰豚画像（huitun）只认小红书博主（设计 §3.9、§6.7）。

同账号的抖音博主不被灰豚画像更新；只有抖音博主时按「未匹配」记数据质量告警。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import DataQualityIssue
from app.modules.importer.adapters.huitun import HuitunImportAdapter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


def _parsed(adapter: HuitunImportAdapter, account: str) -> dict[str, Any]:
    # 灰豚导出的中文表头原样进 adapter
    return adapter.parse_row({"小红书ID": account, "平均点赞": "80", "平均阅读": "2,000"}, None)


async def _dq_count(session: AsyncSession, tenant_id: Any, account: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(DataQualityIssue)
            .where(
                DataQualityIssue.tenant_id == tenant_id,
                DataQualityIssue.source == "huitun",
                DataQualityIssue.entity_ref == account,
            )
        )
    ).scalar_one()


@pytest.mark.usefixtures("tenant_ctx")
class TestHuitunOnlyXiaohongshu:
    async def test_same_account_on_two_platforms_updates_xiaohongshu_only(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        xhs = await blogger_factory.blogger(xiaohongshu_id="K8HT1", platform="小红书")
        dy = await blogger_factory.blogger(xiaohongshu_id="K8HT1", platform="抖音")
        xhs_id, dy_id = xhs.id, dy.id
        adapter = HuitunImportAdapter()
        got_id, _ = await adapter.upsert(
            _parsed(adapter, "K8HT1"), session=session, tenant_id=tenant_a.id, actor_id=None
        )
        assert got_id == xhs_id
        await session.refresh(xhs)
        await session.refresh(dy)
        assert xhs.audience_profile["note_stats"]["avg_likes"] == 80
        assert dy.audience_profile is None
        assert dy.id == dy_id

    async def test_only_douyin_blogger_is_not_matched(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        dy = await blogger_factory.blogger(xiaohongshu_id="K8HT2", platform="抖音")
        dy_id = dy.id
        adapter = HuitunImportAdapter()
        got_id, _ = await adapter.upsert(
            _parsed(adapter, "K8HT2"), session=session, tenant_id=tenant_a.id, actor_id=None
        )
        assert got_id != dy_id
        await session.refresh(dy)
        assert dy.audience_profile is None
        assert await _dq_count(session, tenant_a.id, "K8HT2") == 1
