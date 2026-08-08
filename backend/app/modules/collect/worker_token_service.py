"""U13 WorkerTokenService（签发/吊销/鉴权 + 失败计数自动吊销）。

按 P-U13-01：
- issue：token_urlsafe(32) 明文一次性返回，DB 存 sha256
- authenticate：token_hash 命中 + IP allowlist 校验；失败计数达阈值自动吊销 + 企微告警
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.db import AsyncSessionApp
from app.core.metrics import worker_token_auth_failures_total
from app.core.tenancy import bypass_rls_ctx, tenant_id_ctx
from app.modules.auth.models import User
from app.modules.auth.repository import RoleRepository
from app.modules.collect.config import WORKER_AUTH_FAILURE_THRESHOLD
from app.modules.collect.exceptions import (
    WorkerIpForbidden,
    WorkerTokenInvalid,
)
from app.modules.collect.models import WorkerToken
from app.modules.collect.repository import WorkerTokenRepository
from app.modules.wecom.enums import NotificationType
from app.modules.wecom.notification_service import NotificationService

log = logging.getLogger(__name__)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def ip_is_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """按单 IP/CIDR 白名单匹配；无效或空白名单一律拒绝。"""
    try:
        address = ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if address in ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


class WorkerTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = WorkerTokenRepository(session)
        self._audit = AuditService(session)

    async def issue(
        self, name: str, ip_allowlist: list[str], user: User
    ) -> tuple[WorkerToken, str]:
        raw = secrets.token_urlsafe(32)
        wt = WorkerToken(
            name=name,
            token_hash=hash_token(raw),
            ip_allowlist=list(ip_allowlist),
            is_active=True,
            consecutive_auth_failures=0,
        )
        self._repo.add(wt)
        await self._session.flush()
        await self._audit.log(
            action="worker_token.create",
            resource="worker_token",
            resource_id=wt.id,
            after={"name": name},
            user_id=user.id,
        )
        await self._session.commit()
        return wt, raw

    async def list(self, tenant_id: UUID) -> list[WorkerToken]:
        """返回当前租户 Token 实体；API 仅序列化 WorkerTokenPublic。"""
        return list(await self._repo.list(tenant_id))

    async def revoke(self, token_id: UUID, user: User) -> None:
        wt = await self._repo.get_by_id(token_id, user.tenant_id)
        if wt is None:
            raise WorkerTokenInvalid("Worker token 不存在")
        wt.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="worker_token.revoke",
            resource="worker_token",
            resource_id=wt.id,
            user_id=user.id,
        )
        await self._session.commit()

    async def authenticate(self, raw_token: str, client_ip: str) -> WorkerToken:
        wt = await self._repo.get_active_by_hash(
            hash_token(raw_token), for_update=True
        )
        if wt is None:
            worker_token_auth_failures_total.inc()
            raise WorkerTokenInvalid()
        if not ip_is_allowed(client_ip, list(wt.ip_allowlist or [])):
            await self._register_failure(wt)
            raise WorkerIpForbidden()
        # 成功：重置计数 + last_seen
        wt.consecutive_auth_failures = 0
        wt.last_seen_at = datetime.now(UTC)
        await self._session.commit()
        return wt

    async def _register_failure(self, wt: WorkerToken) -> None:
        worker_token_auth_failures_total.inc()
        wt.consecutive_auth_failures += 1
        revoked = False
        if wt.consecutive_auth_failures >= WORKER_AUTH_FAILURE_THRESHOLD:
            wt.is_active = False
            revoked = True
        tenant_id = wt.tenant_id
        token_id = wt.id
        token_name = wt.name
        await self._session.commit()
        if revoked:
            tenant_token = tenant_id_ctx.set(tenant_id)
            bypass_token = bypass_rls_ctx.set(False)
            try:
                # 鉴权使用 BYPASS 会话；通知必须切回普通应用角色，确保管理员
                # 查询和通知写入均受目标租户的 ORM 过滤与 PostgreSQL RLS 约束。
                async with AsyncSessionApp() as notification_session:
                    admin_ids = await RoleRepository(
                        notification_session
                    ).list_user_ids_by_role_code("admin")
                    await NotificationService(notification_session).notify(
                        admin_ids,
                        f"采集 Worker [{token_name}] 连续鉴权失败已自动吊销，请检查 Worker 配置。",
                        type=NotificationType.SYSTEM.value,
                    )
                    await notification_session.commit()
            except Exception:  # noqa: BLE001
                log.warning(
                    "worker_token_revoke_notify_failed id=%s", str(token_id)
                )
            finally:
                bypass_rls_ctx.reset(bypass_token)
                tenant_id_ctx.reset(tenant_token)


__all__ = ["WorkerTokenService", "hash_token", "ip_is_allowed"]
