"""U07 集成测试：催发扫描（EP08-S05）—— 候选聚合 + 未绑定跳过 + 幂等。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today
from app.modules.wecom.models import WecomContact
from app.modules.wecom.scan_service import WecomScanService


async def _seed_contact(session, tenant_id, blogger_id, external="ext_1") -> None:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        from datetime import datetime, timezone

        session.add(
            WecomContact(
                tenant_id=tenant_id,
                blogger_id=blogger_id,
                external_userid=external,
                matched_wechat="wx",
                bound_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
    finally:
        tenant_id_ctx.reset(tok)


@pytest.mark.integration
@pytest.mark.asyncio
class TestWecomScan:
    async def test_scan_creates_message_for_bound_candidate(
        self,
        session: Any,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            today = get_today()
            style = await product_factory.style(
                style_name="连衣裙", short_name="连衣裙A"
            )
            blogger = await blogger_factory.blogger(nickname="小美")
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                scheduled_publish_date=today + timedelta(days=5),  # 催发
            )
            await _seed_contact(session, tenant_a.id, blogger.id)

            created = await WecomScanService(session).scan_tenant(today)
            assert len(created) == 1

            row = (
                await session.execute(
                    text(
                        "SELECT status, template_type, rendered_content "
                        "FROM wecom_message WHERE id = :id"
                    ),
                    {"id": str(created[0])},
                )
            ).first()
            assert row[0] == "pending"
            assert "小美" in row[2]

            # 幂等：再次扫描不重复建单
            again = await WecomScanService(session).scan_tenant(today)
            assert again == []
        finally:
            tenant_id_ctx.reset(tok)

    async def test_unbound_blogger_notifies_pr(
        self,
        session: Any,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            today = get_today()
            pr = await factory.user(tenant_a)
            style = await product_factory.style(style_name="裙", short_name="裙B")
            blogger = await blogger_factory.blogger(nickname="未绑定博主")
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                scheduled_publish_date=today + timedelta(days=2),  # 重要催发
            )
            # 不建 contact → 应跳过 + 通知 PR

            created = await WecomScanService(session).scan_tenant(today)
            assert created == []

            n = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM notification WHERE user_id = :u "
                        "AND type = 'urge_unbound'"
                    ),
                    {"u": str(pr.id)},
                )
            ).scalar_one()
            assert n == 1
        finally:
            tenant_id_ctx.reset(tok)
