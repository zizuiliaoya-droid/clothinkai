"""8a-5：套装在商品页绑定千牛链接（HTTP 级；设计 §9.1、AC 32、34 ~ 36、N21）。

复用 ``POST /api/platform-products/``（``ops.platform_link:write``）。权限用迁移 seed 出来的
真实角色矩阵：建用户 → ``load_effective_permissions`` → override ``get_current_user_active`` /
``get_current_perms`` / ``get_session``（测试 session），写法照 ``test_import_access_api``。

撞唯一索引时 service 会 ``rollback``（测试 session 里只回滚到上一个保存点），所以 409 用例在
发请求前先 ``commit`` 把种子数据与用户落进已释放的保存点，并提前记下要用的 id。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct

URL = "/api/platform-products/"


def _app() -> Any:
    from app.main import app

    return app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


def _pid() -> str:
    # 13 位千牛 ID 形态；每个用例各自一个，避免撞唯一索引
    return str(10**12 + uuid4().int % 10**12)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.fixture
def as_role(session: AsyncSession, factory: Any, tenant_a: Any) -> Iterator[Callable[[str], Any]]:
    """``await as_role("operations")`` → 之后的请求以该角色的新用户身份发出。"""
    app = _app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override

    async def apply(role_code: str) -> Any:
        role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await factory.user(tenant_a, roles=[role])
        perms = await AuthService(session).load_effective_permissions(user.id)
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        return user

    yield apply
    # app 是模块级单例，不清理会污染后面的用例
    for dep in (get_session, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dep, None)


async def _goods(
    session: AsyncSession,
    tenant: Any,
    styles: list[Any],
    *,
    title: str,
    short_name: str | None = None,
) -> GoodsMain:
    g = GoodsMain(
        tenant_id=tenant.id,
        goods_code=f"SUIT-T{uuid4().hex[:8]}" if len(styles) > 1 else f"G{uuid4().hex[:8]}",
        goods_title=title,
        short_name=short_name,
        is_suit=len(styles) > 1,
    )
    session.add(g)
    await session.flush()
    for i, s in enumerate(styles):
        session.add(
            GoodsStyleItem(tenant_id=tenant.id, goods_main_id=g.id, style_id=s.id, sort_order=i)
        )
    await session.flush()
    return g


async def _suit(session: AsyncSession, tenant: Any, product_factory: Any) -> tuple[Any, Any, Any]:
    a = await product_factory.style()
    b = await product_factory.style()
    suit = await _goods(session, tenant, [a, b], title="春日套装两件套全称", short_name="春日套装")
    return suit, a, b


def _body(platform_id: str, style_id: UUID, goods_main_id: UUID, **kw: Any) -> dict[str, Any]:
    return {
        "platform": "千牛",
        "platform_id": platform_id,
        "style_id": str(style_id),
        "goods_main_id": str(goods_main_id),
        "channel": "普通",
        **kw,
    }


async def _links(session: AsyncSession, platform_id: str) -> list[PlatformProduct]:
    return list(
        (
            await session.execute(
                select(PlatformProduct).where(PlatformProduct.platform_id == platform_id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestSuitLinkBind:
    async def test_ac32_merchandiser_forbidden(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """跟单能改商品也不能绑链接：403，库里没有这条链接。"""
        suit, a, _ = await _suit(session, tenant_a, product_factory)
        pid = _pid()
        await as_role("merchandiser")
        async with _client() as c:
            resp = await c.post(URL, json=_body(pid, a.id, suit.id))
        assert resp.status_code == 403, resp.text
        assert await _links(session, pid) == []

    @pytest.mark.parametrize("role_code", ["operations", "admin"])
    async def test_ac34_bind_to_suit(
        self,
        role_code: str,
        as_role: Any,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """运营、管理员绑成功：归属 = 套装、关联款式 = 所选成员、有审计、链接数 +1。"""
        suit, _, b = await _suit(session, tenant_a, product_factory)
        suit_id, suit_code, b_id = suit.id, suit.goods_code, b.id
        pid = _pid()
        user = await as_role(role_code)
        async with _client() as c:
            before = await c.get("/api/goods/", params={"keyword": suit_code})
            assert before.status_code == 200, before.text
            assert before.json()["items"][0]["link_count"] == 0

            resp = await c.post(URL, json=_body(pid, b_id, suit_id, title="春日套装 链接"))
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["goods_main_id"] == str(suit_id)
            assert body["style_id"] == str(b_id)
            assert body["goods_is_suit"] is True

            after = await c.get("/api/goods/", params={"keyword": suit_code})
            assert after.json()["items"][0]["link_count"] == 1

        (link,) = await _links(session, pid)
        assert link.goods_main_id == suit_id
        assert link.style_id == b_id
        log = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "platform_product.create",
                    AuditLog.resource_id == str(link.id),
                )
            )
        ).scalar_one()
        assert log.user_id == user.id
        assert log.after is not None
        assert log.after["goods_main_id"] == str(suit_id)
        assert log.after["style_id"] == str(b_id)

    async def test_ac35_n21_existing_link_with_owner(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """平台 ID 已绑在别的商品上：409、提示归属商品显示名（不露编码）、原链接不变。"""
        suit, a, _ = await _suit(session, tenant_a, product_factory)
        other_style = await product_factory.style()
        owner = await _goods(
            session, tenant_a, [other_style], title="秋冬羊绒大衣长款全称", short_name="羊绒大衣"
        )
        pid = _pid()
        existing = PlatformProduct(
            tenant_id=tenant_a.id,
            platform="千牛",
            platform_id=pid,
            style_id=other_style.id,
            goods_main_id=owner.id,
            channel="普通",
        )
        session.add(existing)
        await session.flush()
        ids = (suit.id, a.id, owner.id, owner.goods_code, other_style.id, existing.id)
        suit_id, a_id, owner_id, owner_code, other_id, existing_id = ids
        await as_role("operations")
        await session.commit()

        async with _client() as c:
            resp = await c.post(URL, json=_body(pid, a_id, suit_id))
        assert resp.status_code == 409, resp.text
        err = resp.json()
        assert err["code"] == "PLATFORM_PRODUCT_CONFLICT"
        assert err["message"] == "平台 ID 已绑定在「羊绒大衣」上；要改归属请到平台链接页"
        assert owner_code not in err["message"]
        assert err["details"]["existing_goods_name"] == "羊绒大衣"
        assert err["details"]["existing_goods_is_suit"] is False
        assert err["details"]["existing_id"] == str(existing_id)

        (link,) = await _links(session, pid)
        assert link.id == existing_id
        assert link.goods_main_id == owner_id
        assert link.style_id == other_id

    async def test_n21_existing_link_without_owner(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """已有链接还没有归属商品：另一种文案，existing_goods_name 为 null。"""
        suit, a, _ = await _suit(session, tenant_a, product_factory)
        loose = await product_factory.style()
        pid = _pid()
        session.add(
            PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id=pid,
                style_id=loose.id,
                goods_main_id=None,
                channel="普通",
            )
        )
        await session.flush()
        suit_id, a_id = suit.id, a.id
        await as_role("admin")
        await session.commit()

        async with _client() as c:
            resp = await c.post(URL, json=_body(pid, a_id, suit_id))
        assert resp.status_code == 409, resp.text
        err = resp.json()
        assert err["message"] == "这个平台 ID 已存在（还没有归属商品）；要改归属请到平台链接页"
        assert err["details"]["existing_goods_name"] is None
        assert err["details"]["existing_goods_is_suit"] is None
        (link,) = await _links(session, pid)
        assert link.goods_main_id is None

    async def test_ac36_platform_id_normalized(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """前导单引号与首尾空白按规范化后的值保存。"""
        suit, a, _ = await _suit(session, tenant_a, product_factory)
        pid = _pid()
        await as_role("operations")
        async with _client() as c:
            resp = await c.post(URL, json=_body(f"  '{pid}  ", a.id, suit.id))
        assert resp.status_code == 201, resp.text
        assert resp.json()["platform_id"] == pid
        (link,) = await _links(session, pid)
        assert link.platform_id == pid

    @pytest.mark.parametrize("raw", ["'", "  ''  "])
    async def test_ac36_blank_after_normalize_422(
        self, raw: str, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        suit, a, _ = await _suit(session, tenant_a, product_factory)
        await as_role("operations")
        async with _client() as c:
            resp = await c.post(URL, json=_body(raw, a.id, suit.id))
        assert resp.status_code == 422, resp.text
        locs = [e["loc"] for e in resp.json()["details"]["errors"]]
        assert ["body", "platform_id"] in locs

    async def test_style_not_member_422(
        self, as_role: Any, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """关联款式不是该套装的成员：422 INVALID_GOODS_REFERENCE，不建链接。"""
        suit, _, _ = await _suit(session, tenant_a, product_factory)
        outsider = await product_factory.style()
        pid = _pid()
        await as_role("operations")
        async with _client() as c:
            resp = await c.post(URL, json=_body(pid, outsider.id, suit.id))
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "INVALID_GOODS_REFERENCE"
        assert await _links(session, pid) == []
