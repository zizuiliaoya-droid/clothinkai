"""U13 集成测试：worker_token 鉴权 + 调度 + poll/exchange + adapter 入库 + result。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.crypto import encrypt_credential
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.crawler_task_service import CrawlerTaskService
from app.modules.collect.exceptions import (
    CredTokenInvalid,
    WorkerIpForbidden,
    WorkerTokenInvalid,
)
from app.modules.collect.models import CrawlerTask, DataQualityIssue
from app.modules.collect.worker_token_service import WorkerTokenService
from app.modules.credential.models import Credential

pytestmark = pytest.mark.asyncio


async def _make_credential(
    session: AsyncSession, tenant: Any, *, platform: str = "千牛", status: str = "active"
) -> Credential:
    cred = Credential(
        tenant_id=tenant.id,
        platform=platform,
        username=f"acc_{uuid4().hex[:6]}",
        password_ciphertext=encrypt_credential(tenant.id, "secret-pass"),
        status=status,
        consecutive_failures=0,
        privacy_consent_at=datetime.now(UTC),
    )
    session.add(cred)
    await session.flush()
    return cred


class TestWorkerTokenAuth:
    async def test_issue_and_authenticate(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = WorkerTokenService(session)
            wt, raw = await svc.issue("vm-01", ["1.2.3.4"], user)
            assert raw and len(raw) > 20
            ok = await svc.authenticate(raw, "1.2.3.4")
            assert ok.id == wt.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_ip_forbidden(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = WorkerTokenService(session)
            _, raw = await svc.issue("vm-02", ["1.2.3.4"], user)
            with pytest.raises(WorkerIpForbidden):
                await svc.authenticate(raw, "9.9.9.9")
        finally:
            tenant_id_ctx.reset(token)

    async def test_auto_revoke_after_5_failures(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = WorkerTokenService(session)
            wt, raw = await svc.issue("vm-03", ["1.2.3.4"], user)
            for _ in range(5):
                with pytest.raises(WorkerIpForbidden):
                    await svc.authenticate(raw, "9.9.9.9")
            await session.refresh(wt)
            assert wt.is_active is False
            # 已吊销 → 后续认证 401
            with pytest.raises(WorkerTokenInvalid):
                await svc.authenticate(raw, "1.2.3.4")
        finally:
            tenant_id_ctx.reset(token)


class TestScheduleAndPoll:
    async def test_schedule_and_poll_exchange(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            await _make_credential(session, tenant_a)
            wt_svc = WorkerTokenService(session)
            wt, _ = await wt_svc.issue("vm-04", ["1.1.1.1"], user)

            svc = CrawlerTaskService(session)
            n = await svc.schedule_for_tenant(tenant_a.id)
            await session.commit()
            assert n == 1

            # poll
            assignment = await svc.poll_next_task(wt)
            assert assignment is not None
            assert assignment.cred_token
            # poll 响应无明文密码字段
            assert not hasattr(assignment, "password")

            # exchange → 明文
            resp = await svc.exchange_credential(
                assignment.task_id, assignment.cred_token
            )
            assert resp.password == "secret-pass"

            # 再次 exchange → 403（一次性）
            with pytest.raises(CredTokenInvalid):
                await svc.exchange_credential(
                    assignment.task_id, assignment.cred_token
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_exchange_expired(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            cred = await _make_credential(session, tenant_a)
            wt_svc = WorkerTokenService(session)
            wt, _ = await wt_svc.issue("vm-05", ["1.1.1.1"], user)
            # 手动建一个过期 cred_token 的 assigned 任务
            task = CrawlerTask(
                tenant_id=tenant_a.id,
                platform="千牛",
                credential_id=cred.id,
                target_date=datetime.now(UTC).date(),
                status="assigned",
                cred_token="expired-token",
                cred_token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(task)
            await session.flush()
            svc = CrawlerTaskService(session)
            with pytest.raises(CredTokenInvalid):
                await svc.exchange_credential(task.id, "expired-token")
        finally:
            tenant_id_ctx.reset(token)


class TestQianniuUpsert:
    async def test_matched_and_unmatched(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        from app.modules.importer.adapters.qianniu import QianniuImportAdapter
        from app.modules.product.platform_product_models import PlatformProduct

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            pp = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id="P123",
                style_id=style.id,
            )
            session.add(pp)
            await session.flush()

            adapter = QianniuImportAdapter()
            # 匹配
            parsed = adapter.parse_row(
                {"商品ID": "P123", "日期": "2026-06-08", "访客数": "100",
                 "支付金额": "50.00", "支付订单数": "3"},
                None,
            )
            await adapter.upsert(parsed, session=session, tenant_id=tenant_a.id, actor_id=None)
            # 未匹配 → dq issue
            parsed2 = adapter.parse_row(
                {"商品ID": "UNKNOWN", "日期": "2026-06-08", "访客数": "5"}, None
            )
            await adapter.upsert(parsed2, session=session, tenant_id=tenant_a.id, actor_id=None)
            await session.flush()

            cnt = (await session.execute(
                text("SELECT COUNT(*) FROM qianniu_daily WHERE tenant_id=:t"),
                {"t": str(tenant_a.id)},
            )).scalar_one()
            assert cnt == 2

            dq = (await session.execute(
                select(func.count()).select_from(DataQualityIssue).where(
                    DataQualityIssue.tenant_id == tenant_a.id,
                    DataQualityIssue.source == "qianniu",
                    DataQualityIssue.severity == "warning",
                )
            )).scalar_one()
            assert dq == 1
        finally:
            tenant_id_ctx.reset(token)


class TestHuitunUpsert:
    async def test_updates_audience_profile(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        from app.modules.importer.adapters.huitun import HuitunImportAdapter

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(xiaohongshu_id="HT001")
            adapter = HuitunImportAdapter()
            parsed = adapter.parse_row(
                {"小红书ID": "HT001", "平均点赞": "80", "平均阅读": "2000"}, None
            )
            await adapter.upsert(parsed, session=session, tenant_id=tenant_a.id, actor_id=None)
            await session.refresh(blogger)
            assert blogger.audience_profile["note_stats"]["avg_likes"] == 80
            assert blogger.audience_profile["note_stats"]["avg_reads"] == 2000
        finally:
            tenant_id_ctx.reset(token)


class TestReportResult:
    async def test_result_failed_triggers_report_failure(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            cred = await _make_credential(session, tenant_a)
            task = CrawlerTask(
                tenant_id=tenant_a.id,
                platform="千牛",
                credential_id=cred.id,
                target_date=datetime.now(UTC).date(),
                status="exchanged",
            )
            session.add(task)
            await session.flush()
            svc = CrawlerTaskService(session)
            result = await svc.report_result(task.id, "failed", error="登录失败")
            assert result["ok"] is True
            await session.refresh(task)
            assert task.status == "failed"
            await session.refresh(cred)
            assert cred.consecutive_failures == 1
        finally:
            tenant_id_ctx.reset(token)
