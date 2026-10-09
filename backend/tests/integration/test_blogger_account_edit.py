"""编辑博主：账号格式只在新建、以及编辑时账号真的改了的时候校验（fix-blog8b F1）。

生产上大量历史博主的账号是昵称（含中文 / emoji / 空格），前端编辑时把整张表单（含原样的账号）提交；
账号没变就不该因为格式 422，其余字段照常保存。（平台, 账号）唯一性照旧。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.blogger.models import Blogger

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _admin(session: AsyncSession, factory: Any, tenant: Any) -> Any:
    role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
    return await factory.user(tenant, roles=[role])


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限（照 test_blogger_tags_dict._call）。"""
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


async def _account_in_db(session: AsyncSession, blogger_id: Any) -> str:
    return (
        await session.execute(select(Blogger.xiaohongshu_id).where(Blogger.id == blogger_id))
    ).scalar_one()


@pytest.mark.usefixtures("tenant_ctx")
class TestLegacyAccountEdit:
    async def test_unchanged_chinese_account_saves_other_fields(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        account = f"历史昵称号{uuid4().hex[:6]}"
        b = await blogger_factory.blogger(xiaohongshu_id=account)
        b_id = b.id
        user = await _admin(session, factory, tenant_a)
        # 前端编辑：整张表单（含原样的账号）一起提交
        resp = await _call(
            session,
            user,
            "PUT",
            f"/api/bloggers/{b_id}",
            {"xiaohongshu_id": account, "nickname": "新昵称F1", "wechat": "wx_f1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["xiaohongshu_id"], body["nickname"], body["wechat"]) == (
            account,
            "新昵称F1",
            "wx_f1",
        )

    async def test_account_not_sent_saves(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        account = f"表情😀号{uuid4().hex[:6]}"
        b = await blogger_factory.blogger(xiaohongshu_id=account)
        user = await _admin(session, factory, tenant_a)
        resp = await _call(session, user, "PUT", f"/api/bloggers/{b.id}", {"nickname": "只改昵称"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["nickname"] == "只改昵称"
        assert resp.json()["xiaohongshu_id"] == account

    async def test_account_with_edge_spaces_unchanged_kept_verbatim(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        # 库里账号首尾带空格：请求按 str_strip_whitespace 去空格后与库里去空格后相同 → 视为没改，库里原样不动
        account = f" 空格 号{uuid4().hex[:6]} "
        b = await blogger_factory.blogger(xiaohongshu_id=account)
        b_id = b.id
        user = await _admin(session, factory, tenant_a)
        resp = await _call(
            session,
            user,
            "PUT",
            f"/api/bloggers/{b_id}",
            {"xiaohongshu_id": account, "remark": "改备注F1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["remark"] == "改备注F1"
        assert await _account_in_db(session, b_id) == account

    async def test_changed_to_invalid_account_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        account = f"历史昵称号{uuid4().hex[:6]}"
        b = await blogger_factory.blogger(xiaohongshu_id=account)
        b_id = b.id
        user = await _admin(session, factory, tenant_a)
        resp = await _call(
            session,
            user,
            "PUT",
            f"/api/bloggers/{b_id}",
            {"xiaohongshu_id": "另一个中文号", "nickname": "不该保存"},
        )
        assert resp.status_code == 422, resp.text
        assert await _account_in_db(session, b_id) == account

    async def test_valid_to_invalid_account_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger(xiaohongshu_id=f"OK{uuid4().hex[:6]}")
        user = await _admin(session, factory, tenant_a)
        resp = await _call(session, user, "PUT", f"/api/bloggers/{b.id}", {"xiaohongshu_id": "a b"})
        assert resp.status_code == 422, resp.text

    async def test_changed_to_valid_account_ok(
        self, session: AsyncSession, tenant_a: Any, factory: Any, blogger_factory: Any
    ) -> None:
        b = await blogger_factory.blogger(xiaohongshu_id=f"历史昵称号{uuid4().hex[:6]}")
        user = await _admin(session, factory, tenant_a)
        new_account = f"new.f1_{uuid4().hex[:6]}"
        resp = await _call(
            session, user, "PUT", f"/api/bloggers/{b.id}", {"xiaohongshu_id": new_account}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["xiaohongshu_id"] == new_account

    async def test_create_invalid_account_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        user = await _admin(session, factory, tenant_a)
        for bad in ("包含中文", "a b", "号😀"):
            resp = await _call(
                session, user, "POST", "/api/bloggers/", {"xiaohongshu_id": bad, "nickname": "x"}
            )
            assert resp.status_code == 422, (bad, resp.text)
