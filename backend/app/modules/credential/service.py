"""U12 CredentialService（凭据 CRUD + 加密 + 解密审计 + 失败告警）。

按 nfr-design-patterns.md P-U12-01/02：
- create 隐私校验 + 加密 + IntegrityError→409
- _to_public 不可回显（schema 层无密码字段）
- decrypt_for_purpose 审计 success/failed 双分支 + 指标 + 不静默
- report_failure 自动暂停同事务 + 通知 best-effort
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import (
    credential_auto_paused_total,
    credential_decrypt_total,
)
from app.core.security.crypto import (
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
)
from app.modules.auth.models import User
from app.modules.auth.repository import RoleRepository
from app.modules.credential.config import CONSECUTIVE_FAILURE_THRESHOLD
from app.modules.credential.exceptions import (
    CredentialAlreadyExists,
    CredentialNotFound,
    PrivacyConsentRequired,
)
from app.modules.credential.models import Credential
from app.modules.credential.repository import CredentialRepository
from app.modules.credential.schemas import (
    CredentialCreate,
    CredentialPage,
    CredentialPublic,
    CredentialUpdate,
)
from app.modules.wecom.enums import NotificationType
from app.modules.wecom.notification_service import NotificationService

log = logging.getLogger(__name__)


class CredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CredentialRepository(session)
        self._audit = AuditService(session)
        self._roles = RoleRepository(session)
        self._notifier = NotificationService(session)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    async def _require(self, credential_id: UUID) -> Credential:
        cred = await self._repo.get_by_id(credential_id)
        if cred is None:
            raise CredentialNotFound(f"凭据 {credential_id} 不存在")
        return cred

    @staticmethod
    def _to_public(c: Credential) -> CredentialPublic:
        # 永不含 password / password_ciphertext
        return CredentialPublic.model_validate(c)

    # ------------------------------------------------------------------ #
    # create
    # ------------------------------------------------------------------ #
    async def create(
        self, payload: CredentialCreate, user: User
    ) -> CredentialPublic:
        if not payload.privacy_consent:
            raise PrivacyConsentRequired()

        ciphertext = encrypt_credential(
            user.tenant_id, payload.password.get_secret_value()
        )
        cred = Credential(
            platform=payload.platform.value,
            username=payload.username,
            password_ciphertext=ciphertext,
            status="paused",
            consecutive_failures=0,
            privacy_consent_at=datetime.now(UTC),
            remark=payload.remark,
        )
        self._repo.add(cred)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise CredentialAlreadyExists() from exc

        await self._audit.log(
            action="credential.create",
            resource="credential",
            resource_id=cred.id,
            after={"platform": cred.platform, "username": cred.username},
            user_id=user.id,
        )
        await self._session.commit()
        return self._to_public(cred)

    # ------------------------------------------------------------------ #
    # read
    # ------------------------------------------------------------------ #
    async def get(self, credential_id: UUID) -> CredentialPublic:
        return self._to_public(await self._require(credential_id))

    async def list(
        self,
        *,
        tenant_id: UUID,
        platform: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> CredentialPage:
        items, total = await self._repo.list(
            tenant_id=tenant_id,
            platform=platform,
            status=status,
            page=page,
            page_size=page_size,
        )
        return CredentialPage(
            items=[self._to_public(c) for c in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ #
    # update
    # ------------------------------------------------------------------ #
    async def update(
        self, credential_id: UUID, payload: CredentialUpdate, user: User
    ) -> CredentialPublic:
        cred = await self._require(credential_id)
        audit_after: dict = {}
        if payload.password is not None:
            cred.password_ciphertext = encrypt_credential(
                user.tenant_id, payload.password.get_secret_value()
            )
            audit_after["password_changed"] = True
        if payload.remark is not None:
            cred.remark = payload.remark
        await self._session.flush()
        if audit_after:
            await self._audit.log(
                action="credential.update",
                resource="credential",
                resource_id=cred.id,
                after=audit_after,
                user_id=user.id,
            )
        await self._session.commit()
        return self._to_public(cred)

    # ------------------------------------------------------------------ #
    # pause / resume / delete
    # ------------------------------------------------------------------ #
    async def pause(self, credential_id: UUID, user: User) -> CredentialPublic:
        cred = await self._require(credential_id)
        cred.status = "paused"
        await self._session.flush()
        await self._audit.log(
            action="credential.pause",
            resource="credential",
            resource_id=cred.id,
            user_id=user.id,
        )
        await self._session.commit()
        return self._to_public(cred)

    async def resume(self, credential_id: UUID, user: User) -> CredentialPublic:
        cred = await self._require(credential_id)
        cred.status = "active"
        cred.consecutive_failures = 0
        await self._session.flush()
        await self._audit.log(
            action="credential.resume",
            resource="credential",
            resource_id=cred.id,
            user_id=user.id,
        )
        await self._session.commit()
        return self._to_public(cred)

    async def delete(self, credential_id: UUID, user: User) -> None:
        cred = await self._require(credential_id)
        await self._audit.log(
            action="credential.delete",
            resource="credential",
            resource_id=cred.id,
            after={"platform": cred.platform, "username": cred.username},
            user_id=user.id,
        )
        await self._session.delete(cred)
        await self._session.commit()

    # ------------------------------------------------------------------ #
    # decrypt（供 U13 Worker，写审计 + 指标）
    # ------------------------------------------------------------------ #
    async def decrypt_for_purpose(
        self, credential_id: UUID, purpose: str
    ) -> str:
        cred = await self._require(credential_id)
        try:
            plaintext = decrypt_credential(
                cred.tenant_id, cred.id, cred.password_ciphertext, purpose=purpose
            )
        except CredentialDecryptError:
            credential_decrypt_total.labels(cred.platform, "failed").inc()
            await self._audit.log(
                action="credential.decrypt_failed",
                resource="credential",
                resource_id=cred.id,
                after={"purpose": purpose},
            )
            await self._session.commit()
            raise
        credential_decrypt_total.labels(cred.platform, "success").inc()
        await self._audit.log(
            action="credential.decrypt",
            resource="credential",
            resource_id=cred.id,
            after={
                "purpose": purpose,
                "platform": cred.platform,
                "username": cred.username,
            },
        )
        await self._session.commit()
        return plaintext

    # ------------------------------------------------------------------ #
    # 采集失败/成功回调（供 U13）
    # ------------------------------------------------------------------ #
    async def report_failure(
        self, credential_id: UUID, error_reason: str
    ) -> None:
        cred = await self._require(credential_id)
        cred.consecutive_failures += 1
        cred.last_failure_reason = error_reason
        cred.last_failure_at = datetime.now(UTC)
        notify_needed = False
        if cred.consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
            cred.status = "paused"
            credential_auto_paused_total.labels(cred.platform).inc()
            notify_needed = True
        await self._session.flush()
        await self._session.commit()

        if notify_needed:
            try:
                admin_ids = await self._roles.list_user_ids_by_role_code("admin")
                content = (
                    f"{cred.platform} 凭据 {cred.username} 连续 "
                    f"{cred.consecutive_failures} 次采集失败，已自动暂停。"
                    f"请检查平台账号状态。原因：{error_reason}"
                )
                await self._notifier.notify(
                    admin_ids,
                    content,
                    type=NotificationType.CREDENTIAL_FAILURE.value,
                )
                await self._session.commit()
            except Exception:  # noqa: BLE001 通知失败不影响凭据状态
                log.warning(
                    "credential_failure_notify_failed credential_id=%s",
                    str(credential_id),
                )

    async def report_success(self, credential_id: UUID) -> None:
        cred = await self._require(credential_id)
        cred.consecutive_failures = 0
        await self._session.flush()
        await self._session.commit()


__all__ = ["CredentialService"]
