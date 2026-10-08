"""U06a 集成测试：字段映射版本管理（EP07-S09 旧 active 下线）。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import PermissionDeniedError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.importer.exceptions import (
    ImportMappingInvalidError,
    ImportMappingSpecUnavailableError,
)
from app.modules.importer.field_mapping_service import FieldMappingService
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.schemas import FieldMappingColumn, FieldMappingCreate
from app.modules.importer.service import ImportService


def _payload(source: str = "fake_source") -> FieldMappingCreate:
    return FieldMappingCreate(
        source=source,
        columns=[
            FieldMappingColumn(source_col="名称", target_field="name", type="str"),
            FieldMappingColumn(source_col="价格", target_field="price", type="decimal"),
        ],
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestFieldMappingVersioning:
    async def test_create_first_version_is_active_v1(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            # 8a-7：映射读写多收有效权限（来源级判断）
            perms = await AuthService(session).load_effective_permissions(user.id)
            svc = FieldMappingService(session)
            m = await svc.create_version(_payload(), user, perms)
            assert m.version == 1
            assert m.is_active is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_second_version_deactivates_first(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        """新建 v2 → v1 自动下线，仅 v2 active（部分唯一约束 + 业务逻辑）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            # 8a-7：映射读写多收有效权限（来源级判断）
            perms = await AuthService(session).load_effective_permissions(user.id)
            svc = FieldMappingService(session)
            v1 = await svc.create_version(_payload(), user, perms)
            v2 = await svc.create_version(_payload(), user, perms)

            assert v2.version == 2
            assert v2.is_active is True

            active = await svc.get_active("fake_source", user, perms)
            assert active is not None
            assert active.version == 2

            refetched_v1 = await svc.get_by_version("fake_source", v1.version, user)
            assert refetched_v1.is_active is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_versions_desc(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            # 8a-7：映射读写多收有效权限（来源级判断）
            perms = await AuthService(session).load_effective_permissions(user.id)
            svc = FieldMappingService(session)
            await svc.create_version(_payload(), user, perms)
            await svc.create_version(_payload(), user, perms)
            versions = await svc.list_versions("fake_source", user, perms)
            assert [v.version for v in versions] == [2, 1]
        finally:
            tenant_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# 8a-4：带目录的来源（商品资料）——保存按目录校验、mapping-spec、恢复内置默认
# ---------------------------------------------------------------------------

STYLE_SKU = "manual_style_sku"
_MINIMAL = [
    ("款号", "style_code"),
    ("商品编码", "sku_code"),
    ("商品名称", "style_name"),
    ("颜色及规格", "color_size"),
]


def _style_payload(
    *extra: tuple[str, str], drop: str | None = None, **types: str
) -> FieldMappingCreate:
    pairs = [p for p in [*_MINIMAL, *extra] if p[1] != drop]
    return FieldMappingCreate(
        source=STYLE_SKU,
        columns=[
            FieldMappingColumn(source_col=s, target_field=t, type=types.get(t, "str"))
            for s, t in pairs
        ],
    )


@pytest.fixture
def registered() -> Iterator[None]:
    from app.main import register_import_adapters

    saved = dict(ImportAdapterRegistry._adapters)
    register_import_adapters()
    try:
        yield
    finally:
        ImportAdapterRegistry._adapters.clear()
        ImportAdapterRegistry._adapters.update(saved)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


async def _user(
    session: AsyncSession, factory: Any, tenant: Any, role_code: str
) -> tuple[Any, Any]:
    role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
    user = await factory.user(tenant, roles=[role])
    return user, await AuthService(session).load_effective_permissions(user.id)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered", "tenant_ctx")
class TestStyleSkuMappingCatalog:
    async def test_save_validates_against_catalog(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """AC 40：缺款式编码 / 含 category → 422 且不产生新版本；合法的保存后类型由系统定。"""
        user, perms = await _user(session, factory, tenant_a, "merchandiser")
        svc = FieldMappingService(session)
        with pytest.raises(ImportMappingInvalidError):
            await svc.create_version(_style_payload(drop="style_code"), user, perms)
        with pytest.raises(ImportMappingInvalidError, match="category"):
            await svc.create_version(_style_payload(("分类", "category")), user, perms)
        assert await svc.list_versions(STYLE_SKU, user, perms) == []
        m = await svc.create_version(
            _style_payload(("进价", "cost_price"), cost_price="str"), user, perms
        )
        by = {c["target_field"]: c for c in m.mapping_config["columns"]}
        assert by["cost_price"]["type"] == "decimal"

    async def test_spec_and_reset(self, session: AsyncSession, tenant_a: Any, factory: Any) -> None:
        """mapping-spec：目录 + 内置默认 + 生效版本；reset 下线生效版本、历史保留、有审计、新批次回到内置。"""
        user, perms = await _user(session, factory, tenant_a, "operations")
        svc = FieldMappingService(session)
        spec = await svc.spec(STYLE_SKU, user, perms)
        assert spec.active is None
        assert len(spec.targets) == 15
        assert {t.field for t in spec.targets if t.create_only} == {"style_name"}
        assert spec.builtin_columns[0]["source_col"] == "款式编码"

        m = await svc.create_version(_style_payload(), user, perms)
        spec = await svc.spec(STYLE_SKU, user, perms)
        assert spec.active is not None
        assert spec.active.version == m.version
        assert spec.active.created_by_name == (user.display_name or user.username)
        assert [c["source_col"] for c in spec.active.columns][:1] == ["款号"]

        await svc.reset(STYLE_SKU, user, perms)
        assert (await svc.spec(STYLE_SKU, user, perms)).active is None
        assert [v.version for v in await svc.list_versions(STYLE_SKU, user, perms)] == [m.version]
        assert (
            await ImportService(session)._resolve_mapping_version(STYLE_SKU, None, tenant_a.id)
            is None
        )
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "import.field_mapping.reset",
                        AuditLog.resource_id == str(m.id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [a.before for a in audits] == [{"source": STYLE_SKU, "version": m.version}]

    async def test_spec_unavailable_and_forbidden(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        admin, admin_perms = await _user(session, factory, tenant_a, "admin")
        svc = FieldMappingService(session)
        with pytest.raises(ImportMappingSpecUnavailableError):
            await svc.spec("manual_blogger", admin, admin_perms)
        # PR 持 importer.batch:read：能看目录，但不能改 / 重置商品资料映射（Q5）
        pr, pr_perms = await _user(session, factory, tenant_a, "pr")
        assert (await svc.spec(STYLE_SKU, pr, pr_perms)).source == STYLE_SKU
        with pytest.raises(PermissionDeniedError):
            await svc.reset(STYLE_SKU, pr, pr_perms)
        # 设计师既没有 importer.batch:read 也没有 product.import:write：看不到
        designer, designer_perms = await _user(session, factory, tenant_a, "designer")
        with pytest.raises(PermissionDeniedError):
            await svc.spec(STYLE_SKU, designer, designer_perms)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered", "tenant_ctx")
class TestMappingSpecHttp:
    """HTTP：GET /sources/{source}/mapping-spec、POST /field-mappings/reset（AC 39 后端部分）。"""

    @pytest.fixture
    def as_role(
        self, session: AsyncSession, factory: Any, tenant_a: Any
    ) -> Iterator[Callable[[str], Any]]:
        from app.main import app

        async def _session_override() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = _session_override

        async def apply(role_code: str) -> Any:
            user, perms = await _user(session, factory, tenant_a, role_code)
            app.dependency_overrides[get_current_user_active] = lambda: user
            app.dependency_overrides[get_current_perms] = lambda: perms
            return user

        yield apply
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)

    async def test_routes(self, as_role: Callable[[str], Any]) -> None:
        from app.main import app

        await as_role("merchandiser")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/imports/sources/{STYLE_SKU}/mapping-spec")
            assert resp.status_code == 200
            body = resp.json()
            assert body["active"] is None
            assert {t["field"] for t in body["targets"]} >= {"style_code", "external_image_url"}

            resp = await client.post(
                "/api/imports/field-mappings",
                json={
                    "source": STYLE_SKU,
                    "columns": [{"source_col": "分类", "target_field": "category"}],
                },
            )
            assert resp.status_code == 422
            assert resp.json()["code"] == "IMPORT_MAPPING_INVALID"

            payload = _style_payload().model_dump()
            assert (
                await client.post("/api/imports/field-mappings", json=payload)
            ).status_code == 201
            body = (await client.get(f"/api/imports/sources/{STYLE_SKU}/mapping-spec")).json()
            assert body["active"]["version"] == 1

            resp = await client.post(
                "/api/imports/field-mappings/reset", json={"source": STYLE_SKU}
            )
            assert resp.status_code == 200
            assert resp.json() == {"source": STYLE_SKU, "active": None}
            body = (await client.get(f"/api/imports/sources/{STYLE_SKU}/mapping-spec")).json()
            assert body["active"] is None

            resp = await client.get("/api/imports/sources/manual_blogger/mapping-spec")
            assert resp.status_code == 403  # 跟单看不到博主来源

        await as_role("admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/imports/sources/manual_blogger/mapping-spec")
            assert resp.status_code == 404
            assert resp.json()["code"] == "IMPORT_MAPPING_SPEC_UNAVAILABLE"
