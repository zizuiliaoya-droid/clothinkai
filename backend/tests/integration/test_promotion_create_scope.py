"""流程线 PR-5 S4：建单 / 导入站外推广 / 停用收回给主管（设计 4.3、4.4、7.3、11-15、11-58，9.3 C2、C5）。

``promotion_create:write`` 只授 pr_manager（管理员靠 ``*``）：

- ``POST /api/promotions/``、``DELETE /api/promotions/{id}`` 路由层改挂它，PR → 403 ``PERMISSION_DENIED``；
- 导入 ``manual_promotion`` 的上传与重试按来源判它（``importer/access.py``），其他来源（博主）不受影响；
- 停用另按矩阵 ``deactivate`` 判：A ~ D、G 列主管能停，结款单生成后（E / F / H）只有管理员；已停用的单 404（同详情）；
  结款单待付款 / 待财务付款时谁都不能停（收尾 L3，409 ``PROMOTION_SETTLEMENT_IN_PROGRESS``）。

主管新建默认不进发货（``need_shipping`` 不勾 → ``ship_status`` NULL，「待发货」筛不到），勾了才是待发货。
HTTP 级，角色用 ``flow_users``（迁移 seed 的真实角色）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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
    body = f"款式编码,博主\nST{uuid4().hex[:8]},x\n".encode()
    return await c.post(
        "/api/imports/upload",
        data={"source": source},
        files={"file": ("data.csv", body, "text/csv")},
    )


async def _create_body(product_factory: Any, blogger_factory: Any, **kw: Any) -> dict[str, Any]:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return {
        "cooperation_mode": "寄拍",
        "style_id": str(style.id),
        "blogger_id": str(blogger.id),
        "platform": "小红书",
        **kw,
    }


async def _promotion(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any, **kw: Any
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("internal_code", f"DEPC{uuid4().hex[:8].upper()}")
    return await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr, **kw)


async def _is_active(session: AsyncSession, promotion_id: UUID) -> bool:
    return bool(
        (
            await session.execute(
                sa_text("SELECT is_active FROM promotion WHERE id = :pid"), {"pid": promotion_id}
            )
        ).scalar_one()
    )


async def _updated_events(session: AsyncSession, promotion_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT actor_id, payload FROM line_event "
            "WHERE promotion_id = :pid AND event_type = 'promotion.updated' ORDER BY created_at"
        ),
        {"pid": promotion_id},
    )
    return [dict(r) for r in rows.mappings()]


@pytest.mark.usefixtures("tenant_ctx")
class TestCreateScope:
    async def test_pr_cannot_create(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        body = await _create_body(product_factory, blogger_factory)
        await as_user(flow_users.pr)
        async with _client() as c:
            resp = await c.post("/api/promotions/", json=body)
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "PERMISSION_DENIED"
        count = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM promotion WHERE blogger_id = :b"),
                {"b": body["blogger_id"]},
            )
        ).scalar_one()
        assert count == 0

    @pytest.mark.parametrize("who", ["pr_manager", "admin"])
    async def test_manager_create_need_shipping(
        self,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        who: str,
    ) -> None:
        """11-58：不勾 need_shipping → ship_status NULL、「待发货」筛不到；勾了 → 待发货、筛得到。"""
        plain = await _create_body(product_factory, blogger_factory)
        shipped = await _create_body(product_factory, blogger_factory, need_shipping=True)
        await as_user(getattr(flow_users, who))
        async with _client() as c:
            r1 = await c.post("/api/promotions/", json=plain)
            r2 = await c.post("/api/promotions/", json=shipped)
            assert r1.status_code == 201, r1.text
            assert r2.status_code == 201, r2.text
            assert r1.json()["ship_status"] is None
            assert r2.json()["ship_status"] == "待发货"
            resp = await c.get(
                "/api/promotions/", params={"ship_status": "待发货", "page_size": 100}
            )
        assert resp.status_code == 200, resp.text
        ids = {i["id"] for i in resp.json()["items"]}
        assert r2.json()["id"] in ids
        assert r1.json()["id"] not in ids


@pytest.mark.usefixtures("tenant_ctx", "registered")
class TestImportPromotionScope:
    async def test_pr_cannot_import_promotion(
        self, flow_users: Any, as_user: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source="manual_promotion", status="failed")
        await as_user(flow_users.pr)
        async with _client() as c:
            resp = await _upload(c, "manual_promotion")
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"
            resp = await c.post(f"/api/imports/batches/{batch.id}/retry")
            assert resp.status_code == 403, resp.text
            # 仍看得到批次（importer.batch:read 照旧）
            assert (await c.get(f"/api/imports/batches/{batch.id}")).status_code == 200
            # 其他来源不受影响
            resp = await _upload(c, "manual_blogger")
            assert resp.status_code == 202, resp.text
            access = {i["source"]: i for i in (await c.get("/api/imports/access")).json()}
        assert access["manual_promotion"]["can_upload"] is False
        assert access["manual_blogger"]["can_upload"] is True

    @pytest.mark.parametrize("who", ["pr_manager", "admin"])
    async def test_manager_imports_promotion(
        self, flow_users: Any, as_user: Any, import_batch_factory: Any, who: str
    ) -> None:
        batch = await import_batch_factory.batch(source="manual_promotion", status="failed")
        await as_user(getattr(flow_users, who))
        async with _client() as c:
            resp = await _upload(c, "manual_promotion")
            assert resp.status_code == 202, resp.text
            resp = await c.post(f"/api/imports/batches/{batch.id}/retry")
            assert resp.status_code == 200, resp.text
            access = {i["source"]: i for i in (await c.get("/api/imports/access")).json()}
        assert access["manual_promotion"]["can_upload"] is True


@pytest.mark.usefixtures("tenant_ctx")
class TestDeactivateScope:
    async def test_pr_cannot_deactivate(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        await as_user(flow_users.pr)
        async with _client() as c:
            resp = await c.delete(f"/api/promotions/{promo.id}")
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "PERMISSION_DENIED"
        assert await _is_active(session, promo.id) is True

    async def test_manager_deactivates_a_column(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """A 列主管停用 → 204、写 promotion.updated（diff is_active）；再停 → 404。"""
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        pid, manager_id = promo.id, flow_users.pr_manager.id
        await as_user(flow_users.pr_manager)
        async with _client() as c:
            resp = await c.delete(f"/api/promotions/{pid}")
            assert resp.status_code == 204, resp.text
            # 测试共用一个 session：条件 UPDATE 不同步身份映射，让第二次请求像新请求一样从库里读
            session.expire(promo)
            again = await c.delete(f"/api/promotions/{pid}")
        assert await _is_active(session, pid) is False
        events = await _updated_events(session, pid)
        assert len(events) == 1
        assert events[0]["actor_id"] == manager_id
        assert events[0]["payload"] == {"diff": {"is_active": {"before": True, "after": False}}}
        # 已停用的单 get_by_id 按不存在处理（与详情一致），不再写事件
        assert again.status_code == 404, again.text
        assert again.json()["code"] == "PROMOTION_NOT_FOUND"
        assert len(await _updated_events(session, pid)) == 1

    async def test_after_settlement_only_admin(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """11-15：结款单生成后（E 列，待财务付款）主管 403，管理员 204。"""
        promo = await _promotion(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待付款",
        )
        await as_user(flow_users.pr_manager)
        async with _client() as c:
            resp = await c.delete(f"/api/promotions/{promo.id}")
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "PERMISSION_DENIED"
        assert await _is_active(session, promo.id) is True
        await as_user(flow_users.admin)
        async with _client() as c:
            resp = await c.delete(f"/api/promotions/{promo.id}")
        assert resp.status_code == 204, resp.text
        assert await _is_active(session, promo.id) is False


async def _with_settlement(
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
    *,
    promotion_status: str,
    settlement_status: str,
) -> tuple[UUID, UUID]:
    """已发布的推广单 + 它的结款单，两边状态按参数给（结款单状态与推广单结款状态一一对应）。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        internal_code=f"DEPS{uuid4().hex[:8].upper()}",
        publish_status="已发布",
        settlement_status=promotion_status,
    )
    s = await settlement_factory.settlement(
        style=style, blogger=blogger, promotion=promo, settlement_status=settlement_status
    )
    return promo.id, s.id


async def _states(
    session: AsyncSession, promotion_id: UUID, settlement_id: UUID
) -> tuple[str, str]:
    """（推广单结款状态，结款单状态）。"""
    row = await session.execute(
        sa_text(
            "SELECT p.settlement_status, s.settlement_status FROM promotion p "
            "JOIN settlement s ON s.promotion_id = p.id WHERE p.id = :pid AND s.id = :sid"
        ),
        {"pid": promotion_id, "sid": settlement_id},
    )
    a, b = row.one()
    return str(a), str(b)


async def _deactivate(c: AsyncClient, promotion_id: UUID, via: str) -> Any:
    if via == "delete":
        return await c.delete(f"/api/promotions/{promotion_id}")
    return await c.patch(f"/api/promotions/{promotion_id}", json={"is_active": False})


@pytest.mark.usefixtures("tenant_ctx")
class TestDeactivateSettlementGate:
    """收尾 L3：有进行中的结款单（待付款 / 待财务付款）不能停用，DELETE 与 PATCH ``is_active`` 两条路都挡。

    管理员在 E 列有停用格（11-15），所以用管理员发起：挡住它的只能是这道门，不是矩阵。
    """

    @pytest.mark.parametrize("via", ["delete", "patch"])
    @pytest.mark.parametrize("settlement_status", ["待付款", "待财务付款"])
    async def test_in_progress_settlement_blocks_deactivate(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        settlement_status: str,
        via: str,
    ) -> None:
        pid, sid = await _with_settlement(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            promotion_status="待付款",
            settlement_status=settlement_status,
        )
        await as_user(flow_users.admin)
        async with _client() as c:
            resp = await _deactivate(c, pid, via)
        assert resp.status_code == 409, resp.text
        assert (resp.json()["code"], resp.json()["message"]) == (
            "PROMOTION_SETTLEMENT_IN_PROGRESS",
            "这张推广单有进行中的结款单，请先让财务驳回结款单再停用",
        )
        assert await _is_active(session, pid) is True
        assert await _states(session, pid, sid) == ("待付款", settlement_status)
        assert await _updated_events(session, pid) == []

    @pytest.mark.parametrize("via", ["delete", "patch"])
    @pytest.mark.parametrize(
        ("promotion_status", "settlement_status"),
        [("已付款", "已付款"), ("结款驳回", "已驳回")],
    )
    async def test_closed_settlement_allows_deactivate(
        self,
        session: AsyncSession,
        flow_users: Any,
        as_user: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        promotion_status: str,
        settlement_status: str,
        via: str,
    ) -> None:
        """结款单已付款 / 已驳回：管理员照常停用，结款单不动。"""
        pid, sid = await _with_settlement(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            promotion_status=promotion_status,
            settlement_status=settlement_status,
        )
        await as_user(flow_users.admin)
        async with _client() as c:
            resp = await _deactivate(c, pid, via)
        assert resp.status_code == (204 if via == "delete" else 200), resp.text
        assert await _is_active(session, pid) is False
        assert await _states(session, pid, sid) == (promotion_status, settlement_status)
        events = await _updated_events(session, pid)
        assert [e["payload"]["diff"]["is_active"] for e in events] == [
            {"before": True, "after": False}
        ]
