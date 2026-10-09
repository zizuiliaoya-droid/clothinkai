"""8b-3 博主标签字典与系统标签只读（设计 §5）。

- 字典接口 ``/api/blogger-tags``：主管 / 管理员能增删，PR、运营 403；读接口 PR、运营都能看，``can_manage`` 按权限给
- 系统标签（高性价比 / 带货型）进不了字典；字典删除不动博主身上的标签
- 博主建 / 改：改 ``quality_tags`` → 422；类目标签新加字典外词或系统标签词 → 422，保留旧词可以
- 商品字典接口 ``/api/dict-items`` 读不到 / 加不了 / 删不掉 ``blogger_tag``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Role
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_config import SYSTEM_TAGS, TAG_BESTSELLER, TAG_HIGH_VALUE
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
) -> Any:
    """真实路由 + 用户的有效权限（照 test_season_options::TestSeasonOptionsHttp）。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

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
