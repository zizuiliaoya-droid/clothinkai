"""8b-3 博主标签字典与系统标签只读（设计 §5）。

- 字典接口 ``/api/blogger-tags``：主管 / 管理员能增删，PR、运营 403；读接口 PR、运营都能看，``can_manage`` 按权限给
- 系统标签（高性价比 / 带货型）进不了字典；字典删除不动博主身上的标签
- 博主建 / 改：改 ``quality_tags`` → 422；类目标签新加字典外词或系统标签词 → 422，保留旧词可以
- 商品字典接口 ``/api/dict-items`` 读不到 / 加不了 / 删不掉 ``blogger_tag``
- ``/api/blogger-tags/missing``：导入缺的标签按批次现算（计数、行号、补字典后变短、看不到 → 404）
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Role
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_config import SYSTEM_TAGS, TAG_BESTSELLER, TAG_HIGH_VALUE
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.models import FieldMapping, ImportBatch, ImportJob
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.dict_models import DictItem

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _role_user(session: AsyncSession, factory: Any, tenant: Any, role_code: str) -> Any:
    role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
    return await factory.user(tenant, roles=[role])


async def _call(
    session: AsyncSession,
    user: Any,
    method: str,
    path: str,
    json: Any = None,
    perms: EffectivePermissions | None = None,
) -> Any:
    """真实路由 + 用户的有效权限（照 test_season_options::TestSeasonOptionsHttp）。

    ``perms`` 不给 → 按用户的角色现算；给了就用它（造「只有某几个 scope」的查看者）。
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    if perms is None:
        perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def _seed_dict(session: AsyncSession, tenant: Any, *values: str, **kw: Any) -> list[DictItem]:
    items = [
        DictItem(tenant_id=tenant.id, dict_type=kw.get("dict_type", "blogger_tag"), value=v)
        for v in values
    ]
    session.add_all(items)
    await session.flush()
    return items


async def _audits(session: AsyncSession, action: str, resource_id: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == action, AuditLog.resource_id == resource_id)
    return list((await session.execute(stmt)).scalars().all())


async def _dict_values(session: AsyncSession, tenant: Any, dict_type: str) -> list[str]:
    stmt = select(DictItem.value).where(
        DictItem.tenant_id == tenant.id, DictItem.dict_type == dict_type
    )
    return sorted((await session.execute(stmt)).scalars().all())


def test_system_tags_declared() -> None:
    """N4：系统标签集合就是两个质量标签常量。"""
    assert frozenset({TAG_HIGH_VALUE, TAG_BESTSELLER}) == SYSTEM_TAGS


# ---------------------------------------------------------------------------
# 字典接口
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestTagDictApi:
    @pytest.mark.parametrize("role_code", ["pr_manager", "admin"])
    async def test_manager_and_admin_create_and_delete(
        self, session: AsyncSession, tenant_a: Any, factory: Any, role_code: str
    ) -> None:
        user = await _role_user(session, factory, tenant_a, role_code)
        resp = await _call(
            session, user, "POST", "/api/blogger-tags", {"value": "  穿搭8b  ", "sort_order": 3}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["value"] == "穿搭8b"
        assert body["sort_order"] == 3
        tag_id = body["id"]
        logs = await _audits(session, "blogger_tag.create", tag_id)
        assert len(logs) == 1
        assert logs[0].user_id == user.id
        assert logs[0].after == {"value": "穿搭8b", "sort_order": 3}

        listed = await _call(session, user, "GET", "/api/blogger-tags")
        assert listed.status_code == 200, listed.text
        assert listed.json()["can_manage"] is True
        assert [i["value"] for i in listed.json()["items"]] == ["穿搭8b"]

        resp = await _call(session, user, "DELETE", f"/api/blogger-tags/{tag_id}")
        assert resp.status_code == 204, resp.text
        assert await _dict_values(session, tenant_a, "blogger_tag") == []
        logs = await _audits(session, "blogger_tag.delete", tag_id)
        assert len(logs) == 1
        assert logs[0].before == {"value": "穿搭8b", "sort_order": 3}

    @pytest.mark.parametrize("role_code", ["pr", "operations"])
    async def test_pr_and_operations_read_only(
        self, session: AsyncSession, tenant_a: Any, factory: Any, role_code: str
    ) -> None:
        (item,) = await _seed_dict(session, tenant_a, "美妆8b")
        user = await _role_user(session, factory, tenant_a, role_code)

        listed = await _call(session, user, "GET", "/api/blogger-tags")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["can_manage"] is False
        assert [i["value"] for i in body["items"]] == ["美妆8b"]
        assert sorted(body["system_tags"]) == sorted(SYSTEM_TAGS)

        resp = await _call(session, user, "POST", "/api/blogger-tags", {"value": "护肤8b"})
        assert resp.status_code == 403, resp.text
        resp = await _call(session, user, "DELETE", f"/api/blogger-tags/{item.id}")
        assert resp.status_code == 403, resp.text
        assert await _dict_values(session, tenant_a, "blogger_tag") == ["美妆8b"]

    async def test_list_order(self, session: AsyncSession, tenant_a: Any, factory: Any) -> None:
        session.add_all(
            [
                DictItem(tenant_id=tenant_a.id, dict_type="blogger_tag", value="b8", sort_order=1),
                DictItem(tenant_id=tenant_a.id, dict_type="blogger_tag", value="a8", sort_order=1),
                DictItem(tenant_id=tenant_a.id, dict_type="blogger_tag", value="z8", sort_order=0),
                # 别的字典类型不进来
                DictItem(tenant_id=tenant_a.id, dict_type="category", value="c8", sort_order=0),
            ]
        )
        await session.flush()
        user = await _role_user(session, factory, tenant_a, "pr")
        body = (await _call(session, user, "GET", "/api/blogger-tags")).json()
        assert [(i["value"], i["sort_order"]) for i in body["items"]] == [
            ("z8", 0),
            ("a8", 1),
            ("b8", 1),
        ]

    @pytest.mark.parametrize("value", sorted(SYSTEM_TAGS))
    async def test_system_tag_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any, value: str
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        resp = await _call(session, user, "POST", "/api/blogger-tags", {"value": f" {value} "})
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "BLOGGER_TAG_RESERVED"
        assert await _dict_values(session, tenant_a, "blogger_tag") == []

    async def test_duplicate_conflict(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        (item,) = await _seed_dict(session, tenant_a, "美妆8b")
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        resp = await _call(session, user, "POST", "/api/blogger-tags", {"value": "美妆8b"})
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "BLOGGER_TAG_EXISTS"
        # 审计只记成功的
        assert await _audits(session, "blogger_tag.create", str(item.id)) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {"value": "   "},
            {"value": "x" * 33},
            {"value": "ok8b", "sort_order": -1},
            {"value": "ok8b", "sort_order": 10000},
        ],
    )
    async def test_invalid_payload(
        self, session: AsyncSession, tenant_a: Any, factory: Any, payload: dict[str, Any]
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        resp = await _call(session, user, "POST", "/api/blogger-tags", payload)
        assert resp.status_code == 422, resp.text
        assert await _dict_values(session, tenant_a, "blogger_tag") == []

    async def test_delete_not_found_or_other_type(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        (category,) = await _seed_dict(session, tenant_a, "上衣8b", dict_type="category")
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        for tag_id in (str(uuid4()), str(category.id)):
            resp = await _call(session, user, "DELETE", f"/api/blogger-tags/{tag_id}")
            assert resp.status_code == 404, resp.text
            assert resp.json()["code"] == "BLOGGER_TAG_NOT_FOUND"
        assert await _dict_values(session, tenant_a, "category") == ["上衣8b"]

    async def test_delete_keeps_blogger_tags(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        (item,) = await _seed_dict(session, tenant_a, "美妆8b")
        b = await blogger_factory.blogger(category_tags=["美妆8b"])
        b_id = b.id
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        resp = await _call(session, user, "DELETE", f"/api/blogger-tags/{item.id}")
        assert resp.status_code == 204, resp.text
        session.expire_all()
        from app.modules.blogger.models import Blogger

        tags = (
            await session.execute(select(Blogger.category_tags).where(Blogger.id == b_id))
        ).scalar_one()
        assert tags == ["美妆8b"]


# ---------------------------------------------------------------------------
# 博主建 / 改：系统标签只读、类目标签按字典
# ---------------------------------------------------------------------------


def _code(exc: pytest.ExceptionInfo[AppException]) -> str:
    return exc.value.code


@pytest.mark.usefixtures("tenant_ctx")
class TestBloggerTagsCheck:
    async def test_create_with_quality_tags_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "pr")
        with pytest.raises(AppException) as exc:
            await BloggerService(session).create_blogger(
                BloggerCreate(xiaohongshu_id="T8Q1", nickname="x", quality_tags=[TAG_HIGH_VALUE]),
                user,
            )
        assert exc.value.status_code == 422
        assert _code(exc) == "BLOGGER_SYSTEM_TAG_READONLY"

    async def test_update_quality_tags(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger(quality_tags=[TAG_HIGH_VALUE, TAG_BESTSELLER])
        user = await _role_user(session, factory, tenant_a, "admin")
        svc = BloggerService(session)
        # 原样（顺序不同、带空白）传回来：归一后相同 → 放行
        resp = await svc.update_blogger(
            b.id,
            BloggerUpdate(quality_tags=[f" {TAG_BESTSELLER}", TAG_HIGH_VALUE], remark="r8b"),
            user,
        )
        assert resp.remark == "r8b"
        for new in ([TAG_HIGH_VALUE], [], [TAG_HIGH_VALUE, TAG_BESTSELLER, "美妆8b"]):
            with pytest.raises(AppException) as exc:
                await svc.update_blogger(b.id, BloggerUpdate(quality_tags=new), user)
            assert exc.value.status_code == 422
            assert _code(exc) == "BLOGGER_SYSTEM_TAG_READONLY"

    async def test_create_category_tags_by_dict(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        await _seed_dict(session, tenant_a, "美妆8b", "护肤8b")
        user = await _role_user(session, factory, tenant_a, "pr")
        svc = BloggerService(session)
        with pytest.raises(AppException) as exc:
            await svc.create_blogger(
                BloggerCreate(
                    xiaohongshu_id="T8C1",
                    nickname="x",
                    category_tags=["美妆8b", "外星8b", "火星8b"],
                ),
                user,
            )
        assert exc.value.status_code == 422
        assert _code(exc) == "BLOGGER_TAG_NOT_IN_DICT"
        assert exc.value.details["tags"] == ["外星8b", "火星8b"]

        with pytest.raises(AppException) as exc:
            await svc.create_blogger(
                BloggerCreate(xiaohongshu_id="T8C2", nickname="x", category_tags=[TAG_BESTSELLER]),
                user,
            )
        assert _code(exc) == "BLOGGER_SYSTEM_TAG_READONLY"

        resp = await svc.create_blogger(
            BloggerCreate(xiaohongshu_id="T8C3", nickname="x", category_tags=["美妆8b", "护肤8b"]),
            user,
        )
        assert resp.category_tags == ["美妆8b", "护肤8b"]

    async def test_inactive_dict_item_not_counted(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        session.add(
            DictItem(
                tenant_id=tenant_a.id, dict_type="blogger_tag", value="停用8b", is_active=False
            )
        )
        await session.flush()
        user = await _role_user(session, factory, tenant_a, "pr")
        with pytest.raises(AppException) as exc:
            await BloggerService(session).create_blogger(
                BloggerCreate(xiaohongshu_id="T8C4", nickname="x", category_tags=["停用8b"]), user
            )
        assert _code(exc) == "BLOGGER_TAG_NOT_IN_DICT"

    async def test_other_dict_type_or_tenant_not_counted(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any, factory: Any
    ) -> None:
        await _seed_dict(session, tenant_a, "类目8b", dict_type="category")
        token = tenant_id_ctx.set(tenant_b.id)
        try:
            await _seed_dict(session, tenant_b, "别家8b")
        finally:
            tenant_id_ctx.reset(token)
        user = await _role_user(session, factory, tenant_a, "pr")
        with pytest.raises(AppException) as exc:
            await BloggerService(session).create_blogger(
                BloggerCreate(
                    xiaohongshu_id="T8C5", nickname="x", category_tags=["类目8b", "别家8b"]
                ),
                user,
            )
        assert exc.value.details["tags"] == ["类目8b", "别家8b"]

    async def test_update_only_checks_added(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        await _seed_dict(session, tenant_a, "美妆8b")
        # 旧标签「旧词8b」不在字典里（比如字典里删了），保留它可以
        b = await blogger_factory.blogger(category_tags=["旧词8b"])
        user = await _role_user(session, factory, tenant_a, "pr")
        svc = BloggerService(session)
        resp = await svc.update_blogger(
            b.id, BloggerUpdate(category_tags=["旧词8b", "美妆8b"]), user
        )
        assert resp.category_tags == ["旧词8b", "美妆8b"]
        resp = await svc.update_blogger(b.id, BloggerUpdate(category_tags=["美妆8b"]), user)
        assert resp.category_tags == ["美妆8b"]
        # 去掉的旧词再加回来就是「新加」，要过字典
        with pytest.raises(AppException) as exc:
            await svc.update_blogger(b.id, BloggerUpdate(category_tags=["美妆8b", "旧词8b"]), user)
        assert _code(exc) == "BLOGGER_TAG_NOT_IN_DICT"
        assert exc.value.details["tags"] == ["旧词8b"]
        with pytest.raises(AppException) as exc:
            await svc.update_blogger(
                b.id, BloggerUpdate(category_tags=["美妆8b", TAG_HIGH_VALUE]), user
            )
        assert _code(exc) == "BLOGGER_SYSTEM_TAG_READONLY"

    async def test_http_422_code(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger()
        user = await _role_user(session, factory, tenant_a, "pr")
        resp = await _call(
            session, user, "PUT", f"/api/bloggers/{b.id}", {"category_tags": ["外星8b"]}
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "BLOGGER_TAG_NOT_IN_DICT"


# ---------------------------------------------------------------------------
# 商品字典接口碰不到 blogger_tag（§5.2）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestProductDictReserved:
    async def test_list_excludes(self, session: AsyncSession, tenant_a: Any, factory: Any) -> None:
        await _seed_dict(session, tenant_a, "美妆8b")
        await _seed_dict(session, tenant_a, "早春8b", dict_type="season")
        user = await _role_user(session, factory, tenant_a, "admin")
        resp = await _call(session, user, "GET", "/api/dict-items?dict_type=blogger_tag")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
        resp = await _call(session, user, "GET", "/api/dict-items")
        assert resp.status_code == 200, resp.text
        types = {i["dict_type"] for i in resp.json()}
        assert "blogger_tag" not in types
        assert "season" in types

    async def test_create_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "operations")
        resp = await _call(
            session, user, "POST", "/api/dict-items", {"dict_type": "blogger_tag", "value": "x8b"}
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "DICT_TYPE_RESERVED"
        assert await _dict_values(session, tenant_a, "blogger_tag") == []

    async def test_delete_is_not_found(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        (item,) = await _seed_dict(session, tenant_a, "美妆8b")
        user = await _role_user(session, factory, tenant_a, "operations")
        resp = await _call(session, user, "DELETE", f"/api/dict-items/{item.id}")
        assert resp.status_code == 204, resp.text
        assert await _dict_values(session, tenant_a, "blogger_tag") == ["美妆8b"]
        assert await _audits(session, "dict_item.delete", str(UUID(str(item.id)))) == []


# ---------------------------------------------------------------------------
# 导入缺的标签 /api/blogger-tags/missing（设计 §5.3、§6.6）
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def blogger_source() -> Iterator[None]:
    """HTTP 测试不跑 lifespan，博主来源要自己登记（测完还原注册表）。"""
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.register(BloggerImportAdapter())
    yield
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry._adapters.update(saved)


def _raw(tags: str, **extra: Any) -> dict[str, Any]:
    """照 CSV 解析出来的原始行（中文表头、字符串格）；账号 / 昵称是造的。"""
    return {"账号": f"m8{uuid4().hex[:8]}", "昵称": "测试号", "平台": "", "类目标签": tags, **extra}


async def _seed_batch(
    session: AsyncSession,
    tenant: Any,
    jobs: list[tuple[int, str, dict[str, Any]]],
    *,
    source: str = "manual_blogger",
    created_at: datetime = _T0,
    mapping_version: int | None = None,
) -> UUID:
    batch = ImportBatch(
        tenant_id=tenant.id,
        source=source,
        file_hash=uuid4().hex,
        original_filename="bloggers.csv",
        file_r2_key=f"imports/{tenant.id}/{uuid4()}/bloggers.csv",
        mapping_version=mapping_version,
        status="completed",
        created_at=created_at,
    )
    session.add(batch)
    await session.flush()
    session.add_all(
        [
            ImportJob(
                tenant_id=tenant.id,
                batch_id=batch.id,
                row_number=n,
                status=status,
                raw_data=raw,
            )
            for n, status, raw in jobs
        ]
    )
    await session.flush()
    return batch.id


async def _missing(session: AsyncSession, user: Any, batch_id: Any = None, **kw: Any) -> Any:
    path = "/api/blogger-tags/missing"
    if batch_id is not None:
        path += f"?batch_id={batch_id}"
    return await _call(session, user, "GET", path, **kw)


@pytest.mark.usefixtures("tenant_ctx", "blogger_source")
class TestMissingTags:
    async def test_counts_rows_and_order(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """非失败行按标签计次（同一行重复只算一次）、记行号；减去启用字典与系统标签；
        按次数降序、标签升序。"""
        await _seed_dict(session, tenant_a, "美妆8m")
        batch_id = await _seed_batch(
            session,
            tenant_a,
            [
                (1, "success", _raw("美妆8m;外星8m，火星8m")),
                (2, "skipped", _raw("外星8m;外星8m")),
                (3, "failed", _raw("外星8m;水星8m")),  # 失败行不算
                (4, "conflict", _raw(f"{TAG_HIGH_VALUE};火星8m,木星8m")),
                (5, "filled", _raw("外星8m；木星8m")),
                (6, "success", _raw("--")),  # 占位符 = 没给
            ],
        )
        user = await _role_user(session, factory, tenant_a, "pr")
        resp = await _missing(session, user, batch_id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["batch_id"] == str(batch_id)
        assert body["items"] == [
            {"tag": "外星8m", "count": 3, "rows": [1, 2, 5]},
            *sorted(
                [
                    {"tag": "火星8m", "count": 2, "rows": [1, 4]},
                    {"tag": "木星8m", "count": 2, "rows": [4, 5]},
                ],
                key=lambda i: i["tag"],
            ),
        ]

    async def test_shrinks_after_dict_added(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """读时现算：主管补了字典，清单就变短（FI-17）。"""
        batch_id = await _seed_batch(
            session,
            tenant_a,
            [(1, "success", _raw("外星8m;火星8m")), (2, "success", _raw("火星8m"))],
        )
        user = await _role_user(session, factory, tenant_a, "pr_manager")
        before = (await _missing(session, user, batch_id)).json()["items"]
        assert [i["tag"] for i in before] == ["火星8m", "外星8m"]

        resp = await _call(session, user, "POST", "/api/blogger-tags", {"value": "火星8m"})
        assert resp.status_code == 201, resp.text
        after = (await _missing(session, user, batch_id)).json()["items"]
        assert after == [{"tag": "外星8m", "count": 1, "rows": [1]}]

    async def test_rows_capped_at_20(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        batch_id = await _seed_batch(
            session, tenant_a, [(n, "success", _raw("多8m")) for n in range(25, 0, -1)]
        )
        user = await _role_user(session, factory, tenant_a, "operations")
        items = (await _missing(session, user, batch_id)).json()["items"]
        assert items == [{"tag": "多8m", "count": 25, "rows": list(range(1, 21))}]

    async def test_uses_batch_mapping_version(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """按该批的映射版本取类目标签：自定义映射把「标签列8m」映到类目标签。"""
        session.add(
            FieldMapping(
                tenant_id=tenant_a.id,
                source="manual_blogger",
                version=7,
                mapping_config={
                    "columns": [
                        {"source_col": "账号", "target_field": "xiaohongshu_id", "type": "str"},
                        {"source_col": "昵称", "target_field": "nickname", "type": "str"},
                        {
                            "source_col": "标签列8m",
                            "target_field": "category_tags",
                            "type": "list",
                        },
                    ]
                },
                is_active=False,
            )
        )
        await session.flush()
        batch_id = await _seed_batch(
            session,
            tenant_a,
            [(1, "success", _raw("默认列8m", **{"标签列8m": "映射列8m"}))],
            mapping_version=7,
        )
        user = await _role_user(session, factory, tenant_a, "pr")
        items = (await _missing(session, user, batch_id)).json()["items"]
        assert items == [{"tag": "映射列8m", "count": 1, "rows": [1]}]

    async def test_default_latest_blogger_batch(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "pr")
        # 本租户还没有博主导入批次 → 空清单（不报错，弹窗直接显示「没有」）
        resp = await _missing(session, user)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"batch_id": None, "items": []}

        await _seed_batch(session, tenant_a, [(1, "success", _raw("旧批8m"))])
        latest = await _seed_batch(
            session,
            tenant_a,
            [(1, "success", _raw("新批8m"))],
            created_at=_T0 + timedelta(hours=1),
        )
        # 更新的非博主批次不算
        await _seed_batch(
            session,
            tenant_a,
            [(1, "success", _raw("商品8m"))],
            source="manual_style_sku",
            created_at=_T0 + timedelta(hours=2),
        )
        body = (await _missing(session, user)).json()
        assert body == {
            "batch_id": str(latest),
            "items": [{"tag": "新批8m", "count": 1, "rows": [1]}],
        }

    async def test_not_found_cases(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any, factory: Any
    ) -> None:
        """不存在 / 别的租户 / 不是博主来源 / 看不到该来源 → 404，不暴露存在性。"""
        style_batch = await _seed_batch(
            session, tenant_a, [(1, "success", _raw("商品8m"))], source="manual_style_sku"
        )
        token = tenant_id_ctx.set(tenant_b.id)
        try:
            other_batch = await _seed_batch(session, tenant_b, [(1, "success", _raw("别家8m"))])
        finally:
            tenant_id_ctx.reset(token)
        mine = await _seed_batch(session, tenant_a, [(1, "success", _raw("外星8m"))])
        user = await _role_user(session, factory, tenant_a, "admin")
        for batch_id in (uuid4(), style_batch, other_batch):
            resp = await _missing(session, user, batch_id)
            assert resp.status_code == 404, resp.text
            assert resp.json()["code"] == "IMPORT_BATCH_NOT_FOUND"

        # 只有 blogger:read、看不到博主导入批次的查看者
        blind = EffectivePermissions(user_id=str(user.id), scopes=frozenset({"blogger:read"}))
        resp = await _missing(session, user, mine, perms=blind)
        assert resp.status_code == 404, resp.text
        # 不指定批次：只在看得到的博主来源里取最近一批，看不到就当没有
        resp = await _missing(session, user, perms=blind)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"batch_id": None, "items": []}
        # 同一批次，能看的人拿得到（场景有效）
        resp = await _missing(session, user, mine)
        assert resp.status_code == 200, resp.text
        assert [i["tag"] for i in resp.json()["items"]] == ["外星8m"]

    async def test_requires_blogger_read(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        user = await _role_user(session, factory, tenant_a, "warehouse")
        resp = await _missing(session, user)
        assert resp.status_code == 403, resp.text
