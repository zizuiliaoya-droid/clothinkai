from dataclasses import replace
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
