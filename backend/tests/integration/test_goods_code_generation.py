"""补充 3：新建商品不填编码，由系统生成（设计 §11.2，AC 64 ~ 66）。

直接调 ``GoodsService``（照 ``test_goods_crud``）。占用判断含已软删商品；单品加成员变套装不改码；
传了 ``goods_code`` 的旧路径照旧（被占用 409）；插入撞唯一索引（模拟并发）会重新生成。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product import goods_service as goods_service_module
from app.modules.product.goods_codes import NOTICE_OCCUPIED_BY_DELETED
from app.modules.product.goods_models import GoodsMain
from app.modules.product.goods_schemas import GoodsMainCreate, GoodsMainUpdate, GoodsStyleItemIn
from app.modules.product.goods_service import GoodsCodeConflictError, GoodsService
from app.modules.product.platform_product_models import PlatformProduct

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _qianniu_id() -> str:
    return str(10**12 + uuid4().int % 10**12)


@pytest.fixture
def ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


def _code(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:6].upper()}"


def _create(
    *styles: Any, title: str = "新商品全称", short_name: str | None = None, **kw: Any
) -> Any:
    return GoodsMainCreate(
        goods_title=title,
        short_name=short_name,
        items=[GoodsStyleItemIn(style_id=s.id) for s in styles],
        **kw,
    )


@pytest.mark.usefixtures("ctx")
class TestSingleCode:
    async def test_ac64_code_is_style_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        resp = await GoodsService(session).create(
            _create(style), tenant_id=tenant_a.id, user_id=user.id
        )
        assert resp.goods_code == style.style_code
        assert resp.is_suit is False
        assert resp.notices == []

    async def test_ac64_occupied_by_live_goods(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        svc = GoodsService(session)
        first = await svc.create(
            _create(style, title="冰雪飞狐皮草外套长款", short_name="冰雪飞狐"),
            tenant_id=tenant_a.id,
            user_id=user.id,
        )
        second = await svc.create(_create(style), tenant_id=tenant_a.id, user_id=user.id)
        assert first.goods_code == style.style_code
        assert second.goods_code == f"{style.style_code}-2"
        assert second.notices == ["该款已有商品「冰雪飞狐」，已为新商品另行生成内部编码"]
        assert all(style.style_code not in n for n in second.notices)
        third = await svc.create(_create(style), tenant_id=tenant_a.id, user_id=user.id)
        assert third.goods_code == f"{style.style_code}-3"
        codes = {first.goods_code, second.goods_code, third.goods_code}
        assert len(codes) == 3

    async def test_ac64_occupied_by_deleted_goods(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        svc = GoodsService(session)
        old = await svc.create(_create(style), tenant_id=tenant_a.id, user_id=user.id)
        await svc.soft_delete(old.id, user_id=user.id)
        resp = await svc.create(_create(style), tenant_id=tenant_a.id, user_id=user.id)
        assert resp.goods_code == f"{style.style_code}-2"
        assert resp.notices == [NOTICE_OCCUPIED_BY_DELETED]


@pytest.mark.usefixtures("ctx")
class TestSuitCode:
    async def test_ac65_suit_code_generated_unique(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        qianniu_id = _qianniu_id()
        a = await product_factory.style(style_code=_code("B"), qianniu_product_id=qianniu_id)
        b = await product_factory.style(style_code=_code("A"))
        # 成员身上挂着千牛链接也不进编码（生成只看成员款号）
        session.add(
            PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id=qianniu_id,
                style_id=a.id,
                channel="普通",
            )
        )
        await session.flush()
        svc = GoodsService(session)
        first = await svc.create(_create(a, b), tenant_id=tenant_a.id, user_id=user.id)
        low, high = sorted([a.style_code, b.style_code])
        assert first.is_suit is True
        assert first.goods_code == f"SUIT-{low}_{high}"
        assert len(first.goods_code) <= 64
        assert qianniu_id not in first.goods_code
        assert first.notices == []

        # 同一组成员再建一个不撞码；软删的那个也算占用
        second = await svc.create(_create(b, a), tenant_id=tenant_a.id, user_id=user.id)
        assert second.goods_code == f"SUIT-{low}_{high}-2"
        await svc.soft_delete(second.id, user_id=user.id)
        third = await svc.create(_create(a, b), tenant_id=tenant_a.id, user_id=user.id)
        assert third.goods_code == f"SUIT-{low}_{high}-3"

    async def test_ac65_platform_id_like_style_codes_use_serial(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        a = await product_factory.style(style_code=f"{uuid4().int % 10**12:012d}")
        b = await product_factory.style(style_code=_code("S"))
        resp = await GoodsService(session).create(
            _create(a, b), tenant_id=tenant_a.id, user_id=user.id
        )
        assert resp.goods_code.startswith("SUIT-")
        assert a.style_code not in resp.goods_code
        assert len(resp.goods_code) <= 64


@pytest.mark.usefixtures("ctx")
class TestExistingCodesUnchanged:
    async def test_ac66_update_never_changes_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """单品加成员变套装不改码；更新接口传 goods_code 被忽略。"""
        user = await factory.user(tenant_a, roles=[admin_role])
        a = await product_factory.style(style_code=_code("S"))
        b = await product_factory.style(style_code=_code("S"))
        svc = GoodsService(session)
        created = await svc.create(_create(a), tenant_id=tenant_a.id, user_id=user.id)
        payload = GoodsMainUpdate.model_validate(
            {
                "goods_code": "HACKED",
                "items": [{"style_id": str(a.id)}, {"style_id": str(b.id)}],
            }
        )
        updated = await svc.update(created.id, payload, user_id=user.id)
        assert updated.is_suit is True
        assert updated.goods_code == created.goods_code == a.style_code
        assert "goods_code" not in GoodsMainUpdate.model_fields

    async def test_ac66_other_goods_codes_untouched(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """新建（含编码被占用另生成）不改已有商品的编码。"""
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        legacy = GoodsMain(
            tenant_id=tenant_a.id,
            goods_code=style.style_code,
            goods_title="历史商品",
        )
        session.add(legacy)
        await session.flush()
        legacy_id = legacy.id
        await GoodsService(session).create(_create(style), tenant_id=tenant_a.id, user_id=user.id)
        code = (
            await session.execute(select(GoodsMain.goods_code).where(GoodsMain.id == legacy_id))
        ).scalar_one()
        assert code == style.style_code

    async def test_explicit_code_old_path(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        svc = GoodsService(session)
        code = _code("OLD")
        resp = await svc.create(
            _create(style, goods_code=code), tenant_id=tenant_a.id, user_id=user.id
        )
        assert resp.goods_code == code
        assert resp.notices == []
        with pytest.raises(GoodsCodeConflictError):
            await svc.create(
                _create(style, goods_code=code), tenant_id=tenant_a.id, user_id=user.id
            )


@pytest.mark.usefixtures("ctx")
class TestConcurrentRetry:
    async def test_unique_violation_regenerates(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """预检之后编码被并发抢走：插入撞唯一索引 → 回滚保存点、重新生成。"""
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        taken = _code("TAKEN")
        session.add(GoodsMain(tenant_id=tenant_a.id, goods_code=taken, goods_title="被抢的"))
        await session.flush()
        fresh = _code("FRESH")
        answers = iter([(taken, None), (fresh, None)])

        async def fake_generate(repo: Any, member_codes: list[str]) -> tuple[str, str | None]:
            return next(answers)

        monkeypatch.setattr(goods_service_module, "generate_goods_code", fake_generate)
        resp = await GoodsService(session).create(
            _create(style), tenant_id=tenant_a.id, user_id=user.id
        )
        assert resp.goods_code == fresh

    async def test_retries_exhausted_409(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code=_code("S"))
        taken = _code("TAKEN")
        session.add(GoodsMain(tenant_id=tenant_a.id, goods_code=taken, goods_title="被抢的"))
        await session.flush()
        calls: list[int] = []

        async def fake_generate(repo: Any, member_codes: list[str]) -> tuple[str, str | None]:
            calls.append(1)
            return taken, None

        monkeypatch.setattr(goods_service_module, "generate_goods_code", fake_generate)
        with pytest.raises(GoodsCodeConflictError) as exc_info:
            await GoodsService(session).create(
                _create(style), tenant_id=tenant_a.id, user_id=user.id
            )
        assert exc_info.value.code == "GOODS_CODE_CONFLICT"
        assert taken not in exc_info.value.message
        assert len(calls) == goods_service_module.CODE_GENERATE_ATTEMPTS
