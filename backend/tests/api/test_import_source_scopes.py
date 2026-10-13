"""流程线 PR-5 S5：结款单导入按来源判权（设计 4.4、7.6，9.3 C3、C6、C7）。

``manual_settlement`` 的上传 / 重试 / 映射 / 查看 / 裁决只认 ``finance.settlement:pay``
（``importer/access.py::SOURCE_ACCESS``）：财务、管理员能导；PR、主管的 ``importer.batch:write`` 不再够。
财务在 ``role_permission`` 里没有任何 importer 授权，靠来源自己的 ``view`` 只看到结款单批次：
列表只回结款单、带别的 ``source`` → 403，别的来源批次的详情 / 失败明细 → 404（8a 契约：不暴露存在性）。
持 ``importer.batch:read`` 的运营、PR 照旧看全部来源。

HTTP 级，角色用 ``flow_users``（迁移 seed 的真实角色）。结款单导入的行级规则（C4、C8）随 PR-6 / PR-7。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

SETTLEMENT = "manual_settlement"
BLOGGER = "manual_blogger"


class _FakeAttachment:
    """不连 R2 的附件服务（导入上传用）。"""

    def upload_bytes(
        self, data: bytes, *, bucket: Any, key: str, content_type: str = "application/octet-stream"
    ) -> str:
        return key


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
def registered() -> Iterator[None]:
    """注册全部真实 adapter（ASGITransport 不跑 lifespan）；用完原样放回注册表。"""
    from app.main import register_import_adapters
    from app.modules.importer.registry import ImportAdapterRegistry

    saved = dict(ImportAdapterRegistry._adapters)
    register_import_adapters()
    try:
        yield
    finally:
        ImportAdapterRegistry._adapters.clear()
        ImportAdapterRegistry._adapters.update(saved)


@pytest.fixture
def as_user(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """``await as_user(user)`` → 之后的请求以该用户（真实有效权限）发出。"""
    import app.tasks.import_tasks as tasks
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService
    from app.modules.importer.deps import get_import_service
    from app.modules.importer.service import ImportService

    monkeypatch.setattr(tasks.run_import_batch, "delay", lambda *a, **kw: None)
    monkeypatch.setattr(tasks.run_import_batch, "apply_async", lambda *a, **kw: None)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_import_service] = lambda: ImportService(
        session, attachment_service=_FakeAttachment()
    )

    async def apply(user: Any) -> None:
        perms = await AuthService(session).load_effective_permissions(user.id)
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms

    yield apply
    for dep in (get_session, get_import_service, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dep, None)


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _upload(c: AsyncClient, source: str) -> Any:
    # 每次内容不同，避免被哈希去重（409）
    body = f"推广单,结算金额\nP{uuid4().hex[:8]},1\n".encode()
    return await c.post(
        "/api/imports/upload",
        data={"source": source},
        files={"file": ("data.csv", body, "text/csv")},
    )


async def _failed_job(session: AsyncSession, batch: Any, row_number: int) -> None:
    from app.modules.importer.models import ImportJob

    session.add(
        ImportJob(
            tenant_id=batch.tenant_id,
            batch_id=batch.id,
            row_number=row_number,
            status="failed",
            raw_data={"推广单": "P1"},
            error_detail="推广单不存在",
        )
    )
    await session.flush()


async def _listed(c: AsyncClient, **params: Any) -> Any:
    return await c.get("/api/imports/batches", params={"page_size": 100, **params})


@pytest.mark.usefixtures("tenant_ctx", "registered")
class TestSettlementImportScope:
    async def test_finance_has_no_importer_grant(self, session: AsyncSession) -> None:
        """前提（4.8 P5）：财务能导结款单不是靠 importer 授权。"""
        scopes = (
            (
                await session.execute(
                    sa_text(
                        "SELECT p.scope FROM role_permission rp "
                        "JOIN role r ON r.id = rp.role_id "
                        "JOIN permission p ON p.id = rp.permission_id "
                        "WHERE r.code = 'finance' AND p.scope LIKE 'importer%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert scopes == []

    @pytest.mark.parametrize("who", ["pr", "pr_manager"])
    async def test_pr_and_manager_cannot_import_settlement(
        self, flow_users: Any, as_user: Any, import_batch_factory: Any, who: str
    ) -> None:
        batch = await import_batch_factory.batch(source=SETTLEMENT, status="failed")
        await as_user(getattr(flow_users, who))
        async with _client() as c:
            resp = await _upload(c, SETTLEMENT)
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"
            details = resp.json()["details"]
            assert (details["source"], details["required_scope"], details["required_action"]) == (
                SETTLEMENT,
                "finance.settlement",
                "pay",
            )
            resp = await c.post(f"/api/imports/batches/{batch.id}/retry")
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"
            # 仍看得到批次（importer.batch:read 照旧）
            assert (await c.get(f"/api/imports/batches/{batch.id}")).status_code == 200
            # 博主导入不受影响
            resp = await _upload(c, BLOGGER)
            assert resp.status_code == 202, resp.text
            access = {i["source"]: i for i in (await c.get("/api/imports/access")).json()}
        assert access[SETTLEMENT]["can_upload"] is False
        assert access[SETTLEMENT]["can_view"] is True

    async def test_finance_imports_settlement(
        self, session: AsyncSession, flow_users: Any, as_user: Any, import_batch_factory: Any
    ) -> None:
        own = await import_batch_factory.batch(
            source=SETTLEMENT, status="partial", failed=1, created_by=flow_users.finance.id
        )
        await _failed_job(session, own, row_number=3)
        await as_user(flow_users.finance)
        async with _client() as c:
            resp = await _upload(c, SETTLEMENT)
            assert resp.status_code == 202, resp.text
            uploaded = resp.json()["batch_id"]
            resp = await c.get(f"/api/imports/batches/{uploaded}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["source"] == SETTLEMENT
            resp = await c.post(f"/api/imports/batches/{own.id}/retry")
            assert resp.status_code == 200, resp.text
            assert resp.json()["retry_count"] == 1
            resp = await c.get(f"/api/imports/batches/{own.id}/errors/download")
            assert resp.status_code == 200, resp.text
            assert "推广单不存在" in resp.content.decode("utf-8-sig")
            # 别的来源仍不能导
            resp = await _upload(c, BLOGGER)
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"
            access = {i["source"]: i for i in (await c.get("/api/imports/access")).json()}
        assert (access[SETTLEMENT]["can_view"], access[SETTLEMENT]["can_upload"]) == (True, True)
        assert (access[BLOGGER]["can_view"], access[BLOGGER]["can_upload"]) == (False, False)

    async def test_finance_sees_only_settlement_batches(
        self, session: AsyncSession, flow_users: Any, as_user: Any, import_batch_factory: Any
    ) -> None:
        settle = await import_batch_factory.batch(source=SETTLEMENT, status="completed")
        blogger = await import_batch_factory.batch(source=BLOGGER, status="partial", failed=1)
        await _failed_job(session, blogger, row_number=2)
        await as_user(flow_users.finance)
        async with _client() as c:
            resp = await _listed(c)
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert str(settle.id) in {i["id"] for i in items}
            assert {i["source"] for i in items} == {SETTLEMENT}
            resp = await _listed(c, source=SETTLEMENT)
            assert resp.status_code == 200, resp.text
            assert str(settle.id) in {i["id"] for i in resp.json()["items"]}
            resp = await _listed(c, source=BLOGGER)
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"
            # 别的来源的批次：不暴露存在性
            assert (await c.get(f"/api/imports/batches/{blogger.id}")).status_code == 404
            resp = await c.get(f"/api/imports/batches/{blogger.id}/errors/download")
            assert resp.status_code == 404
            assert (await c.post(f"/api/imports/batches/{blogger.id}/retry")).status_code == 404

    async def test_finance_empty_list_not_error(self, flow_users: Any, as_user: Any) -> None:
        """导入记录页：财务还没导过结款单 → 空表，不是 403。"""
        await as_user(flow_users.finance)
        async with _client() as c:
            resp = await _listed(c, source=SETTLEMENT)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

    @pytest.mark.parametrize("who", ["operations", "pr", "pr_manager", "admin"])
    async def test_batch_readers_see_all_sources(
        self, flow_users: Any, as_user: Any, import_batch_factory: Any, who: str
    ) -> None:
        settle = await import_batch_factory.batch(source=SETTLEMENT, status="completed")
        blogger = await import_batch_factory.batch(source=BLOGGER, status="completed")
        await as_user(getattr(flow_users, who))
        async with _client() as c:
            resp = await _listed(c)
            assert resp.status_code == 200, resp.text
            ids = {i["id"] for i in resp.json()["items"]}
            assert {str(settle.id), str(blogger.id)} <= ids
            assert (await c.get(f"/api/imports/batches/{settle.id}")).status_code == 200

    async def test_admin_imports_settlement(
        self, flow_users: Any, as_user: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SETTLEMENT, status="failed")
        await as_user(flow_users.admin)
        async with _client() as c:
            resp = await _upload(c, SETTLEMENT)
            assert resp.status_code == 202, resp.text
            resp = await c.post(f"/api/imports/batches/{batch.id}/retry")
            assert resp.status_code == 200, resp.text
            access = {i["source"]: i for i in (await c.get("/api/imports/access")).json()}
        assert access[SETTLEMENT]["can_upload"] is True
