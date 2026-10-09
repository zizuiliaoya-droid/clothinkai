"""测试工厂遇到不认识的 kwarg 必须直接 TypeError（流程线设计 9.2 第 1 行）。

工厂的 kwarg 清单是手写的；以前多传的 kwarg 被静默忽略，新列漏补进工厂时，
「两边都是 NULL」的断言会假通过。这里对每个工厂各测一条反例（拼错的 kwarg
必须报错、且报错里点出工厂名和那个 kwarg）和一条正例（清单里的 kwarg 照常生效）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

BAD = "shipstatus"

# 调用某个工厂：ctx 里放各工厂 fixture 与预先建好的 style / blogger。
Call = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


async def _user(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["factory"].user(ctx["tenant_a"], **kw)


async def _brand(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].brand(**kw)


async def _style(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].style(**kw)


async def _sku(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].sku(ctx["style"], **kw)


async def _blogger(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["blogger_factory"].blogger(**kw)


async def _promotion(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["promotion_factory"].promotion(
        style=ctx["style"], blogger=ctx["blogger"], **kw
    )


async def _attachment(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["attachment_factory"].attachment(**kw)


async def _settlement(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["settlement_factory"].settlement(
        style=ctx["style"], blogger=ctx["blogger"], **kw
    )


async def _batch(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["import_batch_factory"].batch(**kw)


# (报错里的工厂名, 调用, 正例 kwarg：建出来的对象上同名属性应等于传入值)
CASES: list[tuple[str, Call, dict[str, Any]]] = [
    ("factory.user", _user, {"display_name": "张三"}),
    ("product_factory.brand", _brand, {"brand_name": "品牌甲"}),
    ("product_factory.style", _style, {"style_name": "款式甲"}),
    ("product_factory.sku", _sku, {"color": "蓝"}),
    ("blogger_factory.blogger", _blogger, {"nickname": "博主甲"}),
    ("promotion_factory.promotion", _promotion, {"platform": "抖音"}),
    ("attachment_factory.attachment", _attachment, {"filename": "a.png"}),
    ("settlement_factory.settlement", _settlement, {"note_title": "标题甲"}),
    ("import_batch_factory.batch", _batch, {"original_filename": "b.csv"}),
]
IDS = [c[0] for c in CASES]


@pytest_asyncio.fixture
async def ctx(
    factory: Any,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    attachment_factory: Any,
    settlement_factory: Any,
    import_batch_factory: Any,
) -> dict[str, Any]:
    return {
        "factory": factory,
        "tenant_a": tenant_a,
        "product_factory": product_factory,
        "blogger_factory": blogger_factory,
        "promotion_factory": promotion_factory,
        "attachment_factory": attachment_factory,
        "settlement_factory": settlement_factory,
        "import_batch_factory": import_batch_factory,
        "style": await product_factory.style(),
        "blogger": await blogger_factory.blogger(),
    }


@pytest.mark.parametrize(("name", "call", "good"), CASES, ids=IDS)
async def test_unknown_kwarg_raises(
    ctx: dict[str, Any], name: str, call: Call, good: dict[str, Any]
) -> None:
    with pytest.raises(TypeError) as ei:
        await call(ctx, {**good, BAD: "x"})
    msg = str(ei.value)
    assert name in msg
    assert BAD in msg
    # 认识的 kwarg 不应出现在「不认识」的清单里
    for k in good:
        assert k not in msg


@pytest.mark.parametrize(("name", "call", "good"), CASES, ids=IDS)
async def test_known_kwarg_applies(
    ctx: dict[str, Any], name: str, call: Call, good: dict[str, Any]
) -> None:
    obj = await call(ctx, good)
    for k, v in good.items():
        assert getattr(obj, k) == v, f"{name}.{k}"


async def test_special_kwargs_accepted(ctx: dict[str, Any], session: Any) -> None:
    """不是列的特殊 kwarg：user 的 roles、promotion 的 brand_comment、settlement 的 promotion_id。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role, UserRole

    role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
    user = await ctx["factory"].user(ctx["tenant_a"], roles=[role])
    bound = (
        (await session.execute(select(UserRole.role_id).where(UserRole.user_id == user.id)))
        .scalars()
        .all()
    )
    assert list(bound) == [role.id]

    p = await _promotion(ctx, {"brand_comment": True})
    assert p.brand_comment_attachment_id is not None

    s = await _settlement(ctx, {"promotion_id": p.id})
    assert s.promotion_id == p.id
