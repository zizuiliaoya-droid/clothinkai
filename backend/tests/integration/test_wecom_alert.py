"""U15 集成测试：预警配置 upsert + 异常预警端到端（命中/去重/no_recipient）+ RLS。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.crypto import encrypt_credential
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import QianniuDaily
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.wecom.alert_config_service import AlertConfigService
from app.modules.wecom.alert_models import WecomAlertConfig, WecomAlertLog
from app.modules.wecom.alert_schemas import AlertConfigUpdate
from app.modules.wecom.anomaly_service import AnomalyAlertService
from app.modules.wecom.client import WecomClient
from app.modules.wecom.models import WecomConfig

pytestmark = pytest.mark.asyncio


async def _wecom_config(session: AsyncSession, tenant: Any) -> WecomConfig:
    cfg = WecomConfig(
        tenant_id=tenant.id,
        corp_id="corp",
        agent_id="1000002",
        secret_ciphertext=encrypt_credential(tenant.id, "app-secret"),
        is_active=True,
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def _high_return_style(
    session: AsyncSession, tenant: Any, product_factory: Any, *, pay="1000.00",
    refund="500.00",
) -> Any:
    style = await product_factory.style(tenant=tenant)
    pp = PlatformProduct(
        tenant_id=tenant.id, platform="千牛",
        platform_id=f"P{uuid4().hex[:8]}", style_id=style.id,
    )
    session.add(pp)
    await session.flush()
    session.add(QianniuDaily(
        tenant_id=tenant.id, platform_product_id=pp.id,
        platform_id_snapshot=pp.platform_id, date=date(2026, 6, 8),
        visitors=100, pay_amount=Decimal(pay), pay_orders=10,
        extra={"refund_amount": refund},
    ))
    await session.flush()
    return style


class TestAlertConfig:
    async def test_upsert_and_get_masked(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        operations_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[operations_role])
            svc = AlertConfigService(session)
            await svc.upsert(AlertConfigUpdate(
                control_group_webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123",
                return_rate_threshold=Decimal("0.40"),
                alert_recipients=["u1", "u2", "u1"],  # 去重
                is_enabled=True,
            ), user)
            resp = await svc.get_response()
            assert resp is not None
            assert resp.webhook_configured is True
            assert resp.webhook_mask and resp.webhook_mask.startswith("***")
            assert "qyapi" not in resp.webhook_mask  # 不回显完整 URL
            assert resp.alert_recipients == ["u1", "u2"]
        finally:
            tenant_id_ctx.reset(tok)

    async def test_upsert_overwrites(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        operations_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[operations_role])
            svc = AlertConfigService(session)
            base = AlertConfigUpdate(return_rate_threshold=Decimal("0.40"))
            await svc.upsert(base, user)
            await svc.upsert(
                base.model_copy(update={"return_rate_threshold": Decimal("0.55")}),
                user,
            )
            resp = await svc.get_response()
            assert resp.return_rate_threshold == Decimal("0.5500")
        finally:
            tenant_id_ctx.reset(tok)


class TestAnomalyAlert:
    async def _setup_config(
        self, session: AsyncSession, tenant: Any, recipients: list[str]
    ) -> None:
        session.add(WecomAlertConfig(
            tenant_id=tenant.id,
            return_rate_threshold=Decimal("0.4000"),
            alert_recipients=recipients,
            is_enabled=True,
        ))
        await session.flush()

    async def test_fires_and_dedupes(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any,
        monkeypatch: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            sent: list[tuple] = []

            async def _fake_send(self, touser, markdown):  # noqa: ANN001
                sent.append((touser, markdown))
                return {"errcode": 0}

            monkeypatch.setattr(WecomClient, "send_app_message", _fake_send)

            await _wecom_config(session, tenant_a)
            await self._setup_config(session, tenant_a, ["u1"])
            await _high_return_style(session, tenant_a, product_factory)
            await session.commit()

            svc = AnomalyAlertService(session)
            n1 = await svc.check_and_alert(tenant_a.id)
            await session.commit()
            assert n1 == 1
            assert len(sent) == 1
            assert "退货退款率过高" in sent[0][1]

            # 落 log 1 条
            cnt = (await session.execute(
                select(func.count()).select_from(WecomAlertLog).where(
                    WecomAlertLog.tenant_id == tenant_a.id
                )
            )).scalar_one()
            assert cnt == 1

            # 二次运行 → 去重 skip（不再推送）
            n2 = await svc.check_and_alert(tenant_a.id)
            await session.commit()
            assert n2 == 0
            assert len(sent) == 1
        finally:
            tenant_id_ctx.reset(tok)

    async def test_no_recipient_skips_without_log(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any,
        monkeypatch: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            calls: list = []

            async def _fake_send(self, touser, markdown):  # noqa: ANN001
                calls.append(1)
                return {"errcode": 0}

            monkeypatch.setattr(WecomClient, "send_app_message", _fake_send)

            await self._setup_config(session, tenant_a, [])  # 接收人空
            await _high_return_style(session, tenant_a, product_factory)
            await session.commit()

            n = await AnomalyAlertService(session).check_and_alert(tenant_a.id)
            await session.commit()
            assert n == 0
            assert calls == []  # 未推送
            cnt = (await session.execute(
                select(func.count()).select_from(WecomAlertLog).where(
                    WecomAlertLog.tenant_id == tenant_a.id
                )
            )).scalar_one()
            assert cnt == 0  # 不落 log
        finally:
            tenant_id_ctx.reset(tok)

    async def test_disabled_config_skipped(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            session.add(WecomAlertConfig(
                tenant_id=tenant_a.id,
                return_rate_threshold=Decimal("0.4000"),
                alert_recipients=["u1"], is_enabled=False,
            ))
            await session.flush()
            await session.commit()
            n = await AnomalyAlertService(session).check_and_alert(tenant_a.id)
            assert n == 0
        finally:
            tenant_id_ctx.reset(tok)
