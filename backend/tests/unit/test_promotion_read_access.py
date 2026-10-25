"""R8 共享可见性单元合同：不声称覆盖真实RLS。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.modules.promotion.read_access import PromotionReadAccess, can_read_promotion


@pytest.mark.parametrize(
    "scopes,allowed",
    [
        ({"*"}, True),
        ({"promotion.*:*"}, True),
        ({"promotion:read"}, True),
        ({"report.*:read", "finance.settlement:read"}, False),
        ({"promotion:write"}, False),
        (set(), False),
    ],
)
def test_permission_boundary(scopes, allowed):
    assert can_read_promotion(EffectivePermissions("user", frozenset(scopes))) is allowed


@pytest.mark.asyncio
async def test_no_permission_queries_nothing():
    session = AsyncMock()
    access = PromotionReadAccess(session)
    perms = EffectivePermissions("user", frozenset({"report.*:read"}))
    assert await access.visible_ids([uuid4()], tenant_id=uuid4(), perms=perms) == set()
    with pytest.raises(PermissionDeniedError):
        await access.get(uuid4(), tenant_id=uuid4(), perms=perms)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_batch_has_one_id_only_select_and_shared_guards():
    pid, tenant = uuid4(), uuid4()
    calls = []

    async def execute(stmt):
        calls.append(stmt)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [pid]), scalar_one_or_none=lambda: None
        )

    access = PromotionReadAccess(SimpleNamespace(execute=execute))
    perms = EffectivePermissions("user", frozenset({"promotion:read"}))
    assert await access.visible_ids([pid, uuid4()], tenant_id=tenant, perms=perms) == {pid}
    assert len(calls) == 1
    assert list(calls[0].selected_columns.keys()) == ["id"]
    assert await access.get(pid, tenant_id=tenant, perms=perms) is None
    for statement in calls:
        sql = str(
            statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "promotion.is_active IS true" in sql
        assert str(tenant) in sql and "promotion.tenant_id =" in sql
