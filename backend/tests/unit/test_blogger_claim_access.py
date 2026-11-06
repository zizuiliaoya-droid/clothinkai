from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import FieldPermissionDenied
from app.core.security.field_permissions import FieldPermissionContext, can_read_field
from app.core.security.permissions import EffectivePermissions
from app.modules.blogger.claim_access import (
    BloggerAccessContext,
    can_read_contact,
    can_write_contact,
    contact_field_context,
    ensure_contact_write,
    row_contact_allowed,
)
from app.modules.blogger.models import Blogger
from app.modules.blogger.schemas import BloggerUpdate
from app.modules.blogger.service import BloggerService


def context(*, claimed=False, enabled=True, manager=False, revokes=frozenset()):
    bid, uid, tid = uuid4(), uuid4(), uuid4()
    ctx = BloggerAccessContext(
        uid,
        tid,
        FieldPermissionContext(
            frozenset({"admin"} if manager else {"pr"}), frozenset(), revokes, manager
        ),
        EffectivePermissions(str(uid), frozenset({"blogger:write"})),
        enabled,
        manager,
        False,
        frozenset({bid}),
        frozenset({bid}) if claimed else frozenset(),
        {},
    )
    return bid, ctx


def test_unclaimed_contacts_block_both_read_and_explicit_clear():
    bid, ctx = context()
    assert not can_read_contact(ctx, bid, "wechat")
    assert not can_write_contact(ctx, bid, "phone")
    with pytest.raises(FieldPermissionDenied):
        ensure_contact_write(ctx, bid, {"wechat"})
    ensure_contact_write(ctx, bid, {"nickname"})
    assert not row_contact_allowed(ctx, uuid4())


@pytest.mark.parametrize("manager", [False, True])
def test_claim_or_manager_does_not_override_personal_deny(manager):
    bid, ctx = context(
        claimed=True,
        manager=manager,
        revokes=frozenset({"field.blogger.wechat:read", "field.blogger.phone:write"}),
    )
    assert not can_read_contact(ctx, bid, "wechat")
    assert can_read_contact(ctx, bid, "phone")
    assert not can_write_contact(ctx, bid, "phone")


def test_preparation_keeps_old_access_but_support_stays_readonly():
    bid, ctx = context(enabled=False)
    assert can_read_contact(ctx, bid, "wechat")
    support = replace(ctx, readonly=True)
    assert not can_write_contact(support, bid, "wechat")
    assert not support.can_self_claim


def test_unresolved_failure_export_does_not_fall_back_to_role_contact_access():
    bid, ctx = context(claimed=True)
    assert can_read_field("blogger", "wechat", contact_field_context(ctx, bid))
    assert not can_read_field("blogger", "wechat", contact_field_context(ctx, None))
    assert not can_read_field("blogger", "wechat", contact_field_context(ctx, uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,enabled,claimed,deny_wechat,wechat",
    [
        ("finance", False, False, False, "changed"),
        ("pr", True, False, False, None),
        ("pr", True, True, True, "changed"),
        ("pr", False, False, True, None),
    ],
    ids=["role-deny", "unclaimed-clear", "claimed-personal-deny", "preparation-personal-deny"],
)
async def test_update_aggregates_quote_and_contact_denials(
    monkeypatch, role, enabled, claimed, deny_wechat, wechat
):
    """公开更新入口保留固定错误汇总，并在认领/准备状态下拒绝越权写入或清空。"""
    bid, access = context(enabled=enabled, claimed=claimed)
    revokes = {"field.blogger.quote:write"} if role == "pr" else set()
    if deny_wechat:
        revokes.add("field.blogger.wechat:write")
    access = replace(
        access,
        field_ctx=FieldPermissionContext(frozenset({role}), frozenset(), frozenset(revokes)),
    )
    blogger = Blogger(
        id=bid,
        tenant_id=access.tenant_id,
        xiaohongshu_id="original-id",
        nickname="原博主",
        platform="小红书",
        wechat="original-wechat",
        quote=Decimal("50"),
    )
    session = AsyncMock()
    service = BloggerService(session)
    monkeypatch.setattr(service._repo, "get_by_id", AsyncMock(return_value=blogger))
    monkeypatch.setattr(
        "app.modules.blogger.service.load_blogger_access", AsyncMock(return_value=access)
    )
    user = SimpleNamespace(id=access.user_id, tenant_id=access.tenant_id)
    with pytest.raises(FieldPermissionDenied) as exc_info:
        await service.update_blogger(bid, BloggerUpdate(quote=Decimal("100"), wechat=wechat), user)
    assert exc_info.value.details["fields"] == ["quote", "wechat"]
    assert exc_info.value.details["field"] == "quote"
    assert blogger.wechat == "original-wechat"
    assert blogger.quote == Decimal("50")
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
