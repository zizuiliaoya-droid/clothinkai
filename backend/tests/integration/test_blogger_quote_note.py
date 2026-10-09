"""8b D3 报价备注：字段权限与报价完全相同（复用 ``("blogger", "quote")`` 规则与个人授权），审计只记 changed。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Permission, UserPermissionOverride
from app.modules.blogger.exceptions import FieldPermissionDenied
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_NOTE = "图文500视频800"


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _override(session: AsyncSession, tenant: Any, user: Any, scope: str, effect: str) -> None:
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=perm.id, effect=effect
        )
    )
    await session.flush()


async def _blogger_with_note(blogger_factory: Any, session: AsyncSession) -> Any:
    b = await blogger_factory.blogger()
    b.quote_note = _NOTE
    await session.flush()
    return b


@pytest.mark.usefixtures("tenant_ctx")
class TestRead:
    async def test_operations_cannot_see(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        operations_role: Any,
        blogger_factory: Any,
    ) -> None:
        b = await _blogger_with_note(blogger_factory, session)
        user = await factory.user(tenant_a, roles=[operations_role])
        resp = await BloggerService(session).get_blogger(b.id, user)
        assert resp.quote is None
        assert resp.quote_note is None

    async def test_finance_and_pr_can_see(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        pr_role: Any,
        blogger_factory: Any,
    ) -> None:
        b = await _blogger_with_note(blogger_factory, session)
        svc = BloggerService(session)
        for role in (finance_role, pr_role):
            user = await factory.user(tenant_a, roles=[role])
            assert (await svc.get_blogger(b.id, user)).quote_note == _NOTE

    async def test_quote_read_revoke_hides_note(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any, blogger_factory: Any
    ) -> None:
        b = await _blogger_with_note(blogger_factory, session)
        user = await factory.user(tenant_a, roles=[pr_role])
        await _override(session, tenant_a, user, "field.blogger.quote:read", "revoke")
        resp = await BloggerService(session).get_blogger(b.id, user)
        assert resp.quote is None
        assert resp.quote_note is None


@pytest.mark.usefixtures("tenant_ctx")
class TestWrite:
    async def test_finance_cannot_write(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        blogger_factory: Any,
    ) -> None:
        b = await blogger_factory.blogger()
        user = await factory.user(tenant_a, roles=[finance_role])
        svc = BloggerService(session)
        with pytest.raises(FieldPermissionDenied) as exc:
            await svc.update_blogger(b.id, BloggerUpdate(quote_note="图文1"), user)
        assert exc.value.field == "quote_note"
        assert exc.value.details["entity"] == "blogger"
        with pytest.raises(FieldPermissionDenied):
            await svc.create_blogger(
                BloggerCreate(xiaohongshu_id="Q8N1", nickname="x", quote_note="图文1"), user
            )

    async def test_quote_write_grant_allows(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        blogger_factory: Any,
    ) -> None:
        b = await blogger_factory.blogger()
        user = await factory.user(tenant_a, roles=[finance_role])
        await _override(session, tenant_a, user, "field.blogger.quote:write", "grant")
        resp = await BloggerService(session).update_blogger(
            b.id, BloggerUpdate(quote_note="图文1"), user
        )
        assert resp.quote_note == "图文1"

    async def test_quote_write_revoke_denies_pr(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger()
        user = await factory.user(tenant_a, roles=[pr_role])
        await _override(session, tenant_a, user, "field.blogger.quote:write", "revoke")
        with pytest.raises(FieldPermissionDenied):
            await BloggerService(session).update_blogger(
                b.id, BloggerUpdate(quote_note="图文1"), user
            )

    async def test_pr_write_audit_only_changed(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger()
        b_id = b.id
        user = await factory.user(tenant_a, roles=[pr_role])
        svc = BloggerService(session)
        resp = await svc.update_blogger(b_id, BloggerUpdate(quote_note=_NOTE), user)
        assert resp.quote_note == _NOTE
        created = await svc.create_blogger(
            BloggerCreate(xiaohongshu_id="Q8N2", nickname="x", quote_note=_NOTE), user
        )
        logs = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.resource_id.in_([str(b_id), str(created.id)]))
                )
            )
            .scalars()
            .all()
        )
        by_action = {log.action: log for log in logs}
        assert by_action["blogger.update"].after == {"quote_note_changed": True}
        assert by_action["blogger.update"].before is None
        assert by_action["blogger.create"].after["quote_note_changed"] is True
        for log in logs:
            assert _NOTE not in str(log.before) + str(log.after)
