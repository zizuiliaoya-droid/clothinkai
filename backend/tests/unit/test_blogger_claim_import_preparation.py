"""准备期只豁免认领；通过真实 adapter/diff/applier 验证个人禁写不被补空或覆盖。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.security.field_permissions import FieldPermissionContext
from app.core.security.permissions import EffectivePermissions
from app.modules.blogger import claim_import
from app.modules.blogger.claim_access import BloggerAccessContext
from app.modules.blogger.models import Blogger
from app.modules.importer.adapters import blogger as adapter_module
from app.modules.importer.duplicate_rules import DUPLICATE_RULES, DuplicatePolicy, DuplicateRule
from app.modules.importer.outcome import BatchSeen, ImportRowContext


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["wechat", "phone"])
@pytest.mark.parametrize(
    "policy,original", [(DuplicatePolicy.COMPARE, None), (DuplicatePolicy.OVERWRITE, "original")]
)
async def test_preparation_adapter_respects_personal_contact_deny(
    monkeypatch, field, policy, original
):
    tenant_id, user_id, blogger_id = uuid4(), uuid4(), uuid4()
    blogger = Blogger(
        id=blogger_id,
        tenant_id=tenant_id,
        xiaohongshu_id="existing",
        nickname="同一博主",
        platform="小红书",
        follower_count=100,
        **{field: original},
    )
    actor = SimpleNamespace(id=user_id, tenant_id=tenant_id)
    access = BloggerAccessContext(
        user_id,
        tenant_id,
        FieldPermissionContext(
            frozenset({"pr"}), frozenset(), frozenset({f"field.blogger.{field}:write"})
        ),
        EffectivePermissions(str(user_id), frozenset({"importer.batch:write", "blogger:write"})),
        False,
        False,
        False,
        frozenset({blogger_id}),
        frozenset(),
        {},
    )
    load_actor = AsyncMock(return_value=actor)
    load_access = AsyncMock(return_value=access)
    monkeypatch.setattr(claim_import, "load_claim_actor", load_actor)
    monkeypatch.setattr(claim_import, "load_blogger_access", load_access)
    monkeypatch.setattr(
        adapter_module.BloggerRepository, "get_by_account", AsyncMock(return_value=blogger)
    )
    monkeypatch.setattr(adapter_module.APPLIER, "load_for_update", AsyncMock(return_value=blogger))
    monkeypatch.setattr(
        adapter_module.ConflictRecorder, "lock_pending", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(adapter_module.ConflictRecorder, "touch", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter_module.ConflictRecorder, "record", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter_module, "write_object_audit", AsyncMock())
    monkeypatch.setitem(
        DUPLICATE_RULES,
        "manual_blogger",
        DuplicateRule(policy, key="平台 + 账号", configurable=True),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), flush=AsyncMock())
    ctx = ImportRowContext(tenant_id, "manual_blogger", None, 2, user_id, BatchSeen())

    result = await adapter_module.BloggerImportAdapter().upsert_with_context(
        {
            "xiaohongshu_id": "existing",
            "nickname": "同一博主",
            "platform": "小红书",
            field: "blocked",
            "follower_count": 200,
        },
        session=session,
        ctx=ctx,
    )
    load_actor.assert_awaited_once_with(session, tenant_id, user_id)
    load_access.assert_awaited_once()
    assert getattr(blogger, field) == original
    assert blogger.follower_count == 200  # 允许的资料仍正常经过真实 applier 写入
    assert any("无写入权限" in message for message in result.warnings)


@pytest.mark.asyncio
async def test_preparation_legacy_batch_without_actor_is_explicitly_compatible():
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    ctx = ImportRowContext(uuid4(), "manual_blogger", None, 2, None, BatchSeen())
    parsed = {"nickname": "旧批次", "wechat": "old-import-value"}
    assert await claim_import.prepare_claim_import(session, ctx, parsed, None, []) == parsed
