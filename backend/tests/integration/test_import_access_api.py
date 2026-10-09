"""8a-7：导入接口的来源级权限（HTTP 级；设计 §4.6、§24 NIT 1）。

权限用迁移 seed 出来的真实角色矩阵：建用户 → ``load_effective_permissions`` → override
``get_current_user_active`` / ``get_current_perms`` / ``get_session``（测试 session）。
测到的是「真实角色 × 处理函数里的来源级判断」。

覆盖：AC 55、57、58（上传 / 重试 / 映射 / 批次可见性按来源）、N18（跟单看批次列表只含商品资料）、
``GET /api/imports/access``、AC 54 的 HTTP 用例（运营改成本价、建商品），以及两条护栏：
importer 路由不挂路由级 ``require_permission``、且都依赖 ``get_current_user_active``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.importer.deps import get_import_service
from app.modules.importer.service import ImportService

STYLE_SKU = "manual_style_sku"
OTHER_SOURCES = (
    "manual_promotion",
    "manual_settlement",
    "manual_blogger",
    "qianniu",
    "wanxiangtai",
    "manual_tao_order",
    "manual_brush_order",
)


def _app() -> Any:
    from app.main import app

    return app


class _FakeAttachment:
    """不连 R2 的附件服务（只记录上传的 key）。"""

    def __init__(self) -> None:
        self.uploaded: list[str] = []

    def upload_bytes(
        self, data: bytes, *, bucket: Any, key: str, content_type: str = "application/octet-stream"
    ) -> str:
        self.uploaded.append(key)
        return key


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
def as_role(
    session: AsyncSession, factory: Any, tenant_a: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[str], Any]]:
    """``await as_role("merchandiser")`` → 之后的请求以该角色的新用户身份发出。"""
    import app.tasks.import_tasks as tasks

    app = _app()
    monkeypatch.setattr(tasks.run_import_batch, "delay", lambda batch_id: None)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    attachment = _FakeAttachment()
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_import_service] = lambda: ImportService(
        session, attachment_service=attachment
    )

    async def apply(role_code: str) -> Any:
        role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await factory.user(tenant_a, roles=[role])
        perms = await AuthService(session).load_effective_permissions(user.id)
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        return user

    yield apply
    # app 是模块级单例，不清理会污染后面的用例
    for dep in (get_session, get_import_service, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


def _csv() -> bytes:
    # 每次内容不同，避免被哈希去重（409）
    return f"款式编码,款式名称\nST{uuid4().hex[:8]},款\n".encode()


async def _upload(client: AsyncClient, source: str) -> Any:
    return await client.post(
        "/api/imports/upload",
        data={"source": source},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered", "tenant_ctx")
class TestImportAccessHttp:
    async def test_n18_merchandiser_sees_only_style_sku(
        self, as_role: Any, import_batch_factory: Any
    ) -> None:
        style_batch = await import_batch_factory.batch(source=STYLE_SKU, status="completed")
        qn_batch = await import_batch_factory.batch(source="qianniu", status="completed")
        await as_role("merchandiser")
        async with _client() as c:
            resp = await c.get("/api/imports/batches", params={"page_size": 100})
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            ids = {i["id"] for i in items}
            assert str(style_batch.id) in ids
            assert str(qn_batch.id) not in ids
            assert {i["source"] for i in items} == {STYLE_SKU}

            resp = await c.get("/api/imports/batches", params={"source": "qianniu"})
            assert resp.status_code == 403

            resp = await c.get("/api/imports/field-mappings/active", params={"source": STYLE_SKU})
            assert resp.status_code == 200
            resp = await c.get("/api/imports/field-mappings", params={"source": STYLE_SKU})
            assert resp.status_code == 200
            resp = await c.get("/api/imports/field-mappings/active", params={"source": "qianniu"})
            assert resp.status_code == 403
            resp = await c.get("/api/imports/field-mappings", params={"source": "qianniu"})
            assert resp.status_code == 403

    async def test_ac58_merchandiser_uploads_and_reads_own_source(
        self, as_role: Any, import_batch_factory: Any
    ) -> None:
        qn_batch = await import_batch_factory.batch(source="qianniu", status="failed")
        await as_role("merchandiser")
        async with _client() as c:
            resp = await _upload(c, STYLE_SKU)
            assert resp.status_code == 202, resp.text
            batch_id = resp.json()["batch_id"]
            resp = await c.get(f"/api/imports/batches/{batch_id}")
            assert resp.status_code == 200
            assert resp.json()["source"] == STYLE_SKU

            # 其他来源：不暴露存在性
            assert (await c.get(f"/api/imports/batches/{qn_batch.id}")).status_code == 404
            assert (await c.post(f"/api/imports/batches/{qn_batch.id}/retry")).status_code == 404
            resp = await c.get(f"/api/imports/batches/{qn_batch.id}/errors/download")
            assert resp.status_code == 404
            # 其他来源也不能上传
            assert (await _upload(c, "qianniu")).status_code == 403

    @pytest.mark.parametrize("role_code", ["pr", "pr_manager"])
    async def test_ac57_pr_loses_style_sku_import(
        self, as_role: Any, import_batch_factory: Any, role_code: str
    ) -> None:
        style_batch = await import_batch_factory.batch(source=STYLE_SKU, status="failed")
        await as_role(role_code)
        async with _client() as c:
            assert (await _upload(c, STYLE_SKU)).status_code == 403
            resp = await c.post(f"/api/imports/batches/{style_batch.id}/retry")
            assert resp.status_code == 403
            resp = await c.post(
                "/api/imports/field-mappings",
                json={
                    "source": STYLE_SKU,
                    "columns": [
                        {"source_col": "款式编码", "target_field": "style_code", "type": "str"}
                    ],
                },
            )
            assert resp.status_code == 403
            # 仍能看批次（成本价脱敏由失败明细保证）
            assert (await c.get(f"/api/imports/batches/{style_batch.id}")).status_code == 200
            # 其他来源照常
            resp = await _upload(c, "manual_blogger")
            assert resp.status_code == 202, resp.text

    async def test_ac55_operations_upload(self, as_role: Any) -> None:
        await as_role("operations")
        async with _client() as c:
            for source in OTHER_SOURCES:
                resp = await _upload(c, source)
                assert resp.status_code == 403, (source, resp.text)
            resp = await _upload(c, STYLE_SKU)
            assert resp.status_code == 202, resp.text

    @pytest.mark.parametrize("role_code", ["finance", "pattern_maker", "warehouse", "designer"])
    async def test_no_visible_source_403(self, as_role: Any, role_code: str) -> None:
        await as_role(role_code)
        async with _client() as c:
            assert (await c.get("/api/imports/batches")).status_code == 403

    @pytest.mark.parametrize(
        ("role_code", "style_flags", "blogger_flags"),
        [
            # (can_view, can_upload, can_map, can_resolve)
            ("admin", (True, True, True, True), (True, True, True, True)),
            ("merchandiser", (True, True, True, True), (False, False, False, False)),
            ("operations", (True, True, True, True), (True, False, False, False)),
            ("pr", (True, False, False, False), (True, True, False, True)),
            ("pr_manager", (True, False, False, False), (True, True, True, True)),
            ("designer", (False, False, False, False), (False, False, False, False)),
            ("finance", (False, False, False, False), (False, False, False, False)),
        ],
    )
    async def test_access_endpoint(
        self,
        as_role: Any,
        role_code: str,
        style_flags: tuple[bool, ...],
        blogger_flags: tuple[bool, ...],
    ) -> None:
        await as_role(role_code)
        async with _client() as c:
            resp = await c.get("/api/imports/access")
        assert resp.status_code == 200
        by_source = {i["source"]: i for i in resp.json()}
        keys = ("can_view", "can_upload", "can_map", "can_resolve")
        assert tuple(by_source[STYLE_SKU][k] for k in keys) == style_flags
        assert tuple(by_source["manual_blogger"][k] for k in keys) == blogger_flags
        assert by_source[STYLE_SKU]["label"] == "商品资料"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestOperationsProductWriteHttp:
    """AC 54：运营改成本价、新建商品（路由级 product:write / product.goods:write）。"""

    async def test_operations_updates_sku_cost_price(
        self, as_role: Any, product_factory: Any, session: AsyncSession
    ) -> None:
        style = await product_factory.style(style_code="OPS8A01")
        sku = await product_factory.sku(style, sku_code="OPS8A01-红-M")
        user = await as_role("operations")
        async with _client() as c:
            resp = await c.put(f"/api/skus/{sku.id}", json={"cost_price": "65.00"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["cost_price"] == "65.00"
        log = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "sku.update", AuditLog.resource_id == str(sku.id)
                )
            )
        ).scalar_one()
        assert log.user_id == user.id
        assert log.after == {"cost_price_changed": True}

    async def test_operations_creates_goods(self, as_role: Any, product_factory: Any) -> None:
        style = await product_factory.style(style_code="OPS8A02")
        await as_role("operations")
        async with _client() as c:
            resp = await c.post(
                "/api/goods/",
                json={
                    "goods_code": "OPS8A02",
                    "goods_title": "运营新建的商品",
                    "items": [{"style_id": str(style.id)}],
                },
            )
        assert resp.status_code == 201, resp.text


async def _conflict(session: AsyncSession, tenant: Any, source: str, batch_id: Any) -> Any:
    from app.modules.importer.models import ImportConflict

    c = ImportConflict(
        tenant_id=tenant.id,
        source=source,
        batch_id=batch_id,
        row_numbers=[1],
        object_type="sku" if source == STYLE_SKU else "blogger",
        object_id=uuid4(),
        object_key="k",
        object_label="对象",
        kind="fields",
        fields=[{"field": "remark", "label": "备注", "system": "a", "file": "b"}],
    )
    session.add(c)
    await session.flush()
    return c


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered", "tenant_ctx")
class TestConflictAccessHttp:
    """8a-6：/access 的 configurable、冲突接口与 notes 按来源可见。"""

    async def test_access_configurable(self, as_role: Any) -> None:
        await as_role("admin")
        async with _client() as c:
            resp = await c.get("/api/imports/access")
        assert resp.status_code == 200
        configurable = {i["source"] for i in resp.json() if i["configurable"]}
        assert configurable == {STYLE_SKU, "manual_blogger", "huitun_douyin"}

    async def test_conflicts_visible_by_source(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        style_batch = await import_batch_factory.batch(source=STYLE_SKU, status="completed")
        blogger_batch = await import_batch_factory.batch(
            source="manual_blogger", status="completed"
        )
        style_c = await _conflict(session, tenant_a, STYLE_SKU, style_batch.id)
        blogger_c = await _conflict(session, tenant_a, "manual_blogger", blogger_batch.id)

        await as_role("merchandiser")
        async with _client() as c:
            resp = await c.get("/api/imports/conflicts", params={"page_size": 100})
            assert resp.status_code == 200, resp.text
            ids = {i["id"] for i in resp.json()["items"]}
            assert str(style_c.id) in ids
            assert str(blogger_c.id) not in ids
            assert (
                await c.get("/api/imports/conflicts", params={"source": "manual_blogger"})
            ).status_code == 403
            resp = await c.get("/api/imports/conflicts/summary", params={"source": STYLE_SKU})
            assert resp.status_code == 200
            assert resp.json()["pending"] >= 1
            assert (
                await c.get("/api/imports/conflicts/summary", params={"source": "manual_blogger"})
            ).status_code == 403
            # 看不到的来源：裁决按不存在处理
            resp = await c.post(
                "/api/imports/conflicts/resolve",
                json={"decision": "keep", "items": [{"id": str(blogger_c.id)}]},
            )
            assert resp.status_code == 404
            assert resp.json()["code"] == "IMPORT_CONFLICT_NOT_FOUND"
            # 参数不合法由 FastAPI 返回 422
            bad = await c.get("/api/imports/conflicts", params={"status": "nope"})
            assert bad.status_code == 422
            bad = await c.get("/api/imports/conflicts", params={"object_type": "brand"})
            assert bad.status_code == 422
            # 批次详情带待处理冲突条数
            resp = await c.get(f"/api/imports/batches/{style_batch.id}")
            assert resp.json()["pending_conflicts"] == 1
            resp = await c.get("/api/imports/batches", params={"page_size": 100})
            by_id = {i["id"]: i for i in resp.json()["items"]}
            assert by_id[str(style_batch.id)]["pending_conflicts"] == 1

        await as_role("pr")
        async with _client() as c:
            resp = await c.get(
                "/api/imports/conflicts", params={"source": "manual_blogger", "page_size": 100}
            )
            assert resp.status_code == 200
            assert str(blogger_c.id) in {i["id"] for i in resp.json()["items"]}
            resp = await c.post(
                "/api/imports/conflicts/resolve",
                json={"decision": "keep", "items": [{"id": str(style_c.id)}]},
            )
            assert resp.status_code == 403
            resp = await c.get(
                "/api/imports/conflicts/download", params={"source": "manual_blogger"}
            )
            assert resp.status_code == 200
            assert resp.content.startswith("\ufeff".encode())

    async def test_batch_notes(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        from app.modules.importer.models import ImportJob

        batch = await import_batch_factory.batch(source=STYLE_SKU, status="completed")
        qn_batch = await import_batch_factory.batch(source="qianniu", status="completed")
        for row_number, status_, notes in (
            (1, "skipped", None),
            (
                2,
                "filled",
                {
                    "warnings": ["提示"],
                    "filled": [
                        {"object_type": "sku", "object_label": "SKU-1", "fields": ["base_price"]}
                    ],
                },
            ),
            (3, "conflict", {"warnings": ["冲突提示"], "filled": []}),
        ):
            session.add(
                ImportJob(
                    tenant_id=tenant_a.id,
                    batch_id=batch.id,
                    row_number=row_number,
                    status=status_,
                    raw_data={},
                    notes=notes,
                )
            )
        await session.flush()
        await as_role("merchandiser")
        async with _client() as c:
            resp = await c.get(f"/api/imports/batches/{batch.id}/notes")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["total"] == 2
            assert [i["row_number"] for i in body["items"]] == [2, 3]
            assert body["items"][0]["filled"][0]["fields"] == ["base_price"]
            assert body["items"][1]["warnings"] == ["冲突提示"]
            assert (await c.get(f"/api/imports/batches/{qn_batch.id}/notes")).status_code == 404


def _all_calls(dependant: Dependant) -> set[Any]:
    """递归取一个路由的全部依赖（含嵌套）的 call。

    设计 §24 写的是 ``get_flat_dependant``，但 FastAPI 0.115 的它只拍平参数、不保留
    ``dependencies``（返回值的 dependencies 为空），所以这里自己递归。
    """
    calls: set[Any] = set()
    for sub in dependant.dependencies:
        calls.add(sub.call)
        calls |= _all_calls(sub)
    return calls


def _import_routes() -> list[APIRoute]:
    routes = [
        r for r in _app().routes if isinstance(r, APIRoute) and r.path.startswith("/api/imports")
    ]
    assert len(routes) >= 9, "importer 路由少了，测试前提不成立"
    return routes


@pytest.mark.integration
class TestImportRouteGuards:
    def test_no_route_level_permission(self) -> None:
        """importer 路由一律不挂路由级 require_permission（会先把跟单挡在外面，§4.6）。"""
        offenders = []
        for route in _import_routes():
            for dep in route.dependencies:
                qualname = getattr(dep.dependency, "__qualname__", "")
                if qualname == "require_permission.<locals>._checker":
                    offenders.append(f"{sorted(route.methods)} {route.path}")
        assert offenders == []

    def test_every_import_route_requires_auth(self) -> None:
        """§24 NIT 1：撤掉路由级依赖后，每个 importer 路由仍依赖 get_current_user_active。"""
        missing = []
        for route in _import_routes():
            if get_current_user_active not in _all_calls(route.dependant):
                missing.append(f"{sorted(route.methods)} {route.path}")
        assert missing == []
