"""U12 集成测试：CredentialService 端到端。

覆盖创建加密 / 隐私 422 / 重复 409 / 解密审计 / 连续失败自动暂停+通知 / RLS 隔离。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.credential.exceptions import (
    CredentialAlreadyExists,
    PrivacyConsentRequired,
)
from app.modules.credential.schemas import CredentialCreate, CredentialUpdate
from app.modules.credential.service import CredentialService

pytestmark = pytest.mark.asyncio


def _payload(**kw: Any) -> CredentialCreate:
    base: dict[str, Any] = {
        "platform": "千牛",
        "username": "shop_account_001",
        "password": "secret-pass-123",
        "privacy_consent": True,
    }
    base.update(kw)
    return CredentialCreate(**base)


class TestCreate:
    async def test_create_encrypts_and_paused(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            assert resp.status == "paused"
            assert resp.username == "shop_account_001"
            # 响应无密码字段
            assert not hasattr(resp, "password")
            assert "password" not in resp.model_dump()
        finally:
            tenant_id_ctx.reset(token)

    async def test_privacy_consent_required(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            with pytest.raises(PrivacyConsentRequired):
                await svc.create(_payload(privacy_consent=False), user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_duplicate_returns_409(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            await svc.create(_payload(), user)
            with pytest.raises(CredentialAlreadyExists):
                await svc.create(_payload(), user)
        finally:
            tenant_id_ctx.reset(token)


class TestDecryptAndAudit:
    async def test_decrypt_writes_audit(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        from app.modules.auth.models import AuditLog

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            plaintext = await svc.decrypt_for_purpose(resp.id, purpose="crawler_qianniu")
            assert plaintext == "secret-pass-123"
            cnt = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "credential.decrypt",
                        AuditLog.resource_id == str(resp.id),
                    )
                )
            ).scalar_one()
            assert cnt == 1
        finally:
            tenant_id_ctx.reset(token)


class TestFailureAlert:
    async def test_consecutive_failures_auto_pause_and_notify(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        from app.modules.wecom.models import Notification

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            await svc.resume(resp.id, user)  # active

            for _ in range(3):
                await svc.report_failure(resp.id, "登录失败：验证码")

            after = await svc.get(resp.id)
            assert after.status == "paused"
            assert after.consecutive_failures == 3

            # admin 收到通知
            notif_cnt = (
                await session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.user_id == user.id,
                        Notification.type == "credential_failure",
                    )
                )
            ).scalar_one()
            assert notif_cnt >= 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_report_success_resets(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            await svc.report_failure(resp.id, "err")
            await svc.report_success(resp.id)
            after = await svc.get(resp.id)
            assert after.consecutive_failures == 0
        finally:
            tenant_id_ctx.reset(token)


class TestUpdateAndPause:
    async def test_update_password_reencrypts(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            await svc.update(
                resp.id, CredentialUpdate(password="new-pass-456"), user
            )
            plaintext = await svc.decrypt_for_purpose(resp.id, purpose="test")
            assert plaintext == "new-pass-456"
        finally:
            tenant_id_ctx.reset(token)

    async def test_delete_removes(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        from app.modules.credential.exceptions import CredentialNotFound

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = CredentialService(session)
            resp = await svc.create(_payload(), user)
            await svc.delete(resp.id, user)
            with pytest.raises(CredentialNotFound):
                await svc.get(resp.id)
        finally:
            tenant_id_ctx.reset(token)
