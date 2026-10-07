"""8a-2 商品图由成员款式派生（AC 12、AC 14 = 设计 §7.2）。

- 商品接口（列表与详情）返回 ``images``：按成员顺序取启用成员、有图的才放进来；单品最多 1 张、
  套装缺图不占位、都没有为 ``[]``；``goods_main.main_image_key`` 已废弃，有值也不再出现
- 投产（实时与汇总两条读路径共用 ``GOODS_META_COLUMNS``）的商品主图只取成员款式已上传的主图

R2 用假 client（只做本地签名），不向任何真实存储发请求。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.production_service as production_mod
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import QianniuDaily
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.goods_repository import GoodsListFilters
from app.modules.product.goods_service import GoodsService
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.production_service import ProductionService
from app.modules.report.summary_refresh_service import SummaryRefreshService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXT = "https://img.example.invalid/{}.jpg"


class _FakeSigner:
    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{kw['Params']['Key']}"

    def put_object(self, **_kw: Any) -> dict[str, Any]:  # pragma: no cover - 不应被调用
        raise AssertionError("商品图派生不应写 R2")


def _signed(key: str) -> str:
    return f"https://fake-r2.local/{key}"


@pytest.fixture(autouse=True)
def _fake_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeSigner(), raising=False)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


async def _goods(
    session: AsyncSession,
    tenant: Any,
    code: str,
    members: list[Any],
    *,
    inactive: frozenset[int] = frozenset(),
    **kw: Any,
) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=f"{code} 全称",
        is_suit=len(members) >= 2,
        **kw,
    )
    session.add(goods)
    await session.flush()
    for order, style in enumerate(members):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id,
                goods_main_id=goods.id,
                style_id=style.id,
                sort_order=order,
                is_active=order not in inactive,
            )
        )
    await session.flush()
    return goods


def _pairs(images: list[Any]) -> list[tuple[str, str, str]]:
    return [(i.style_code, i.url, i.source) for i in images]


class TestGoodsImages:
    async def test_list_and_detail(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any, tenant_ctx: None
    ) -> None:
        tag = uuid4().hex[:6]
        up = await product_factory.style(style_code=f"GI{tag}U", main_image_key=f"k/{tag}u")
        ext = await product_factory.style(style_code=f"GI{tag}E")
        ext.external_image_url = EXT.format(tag)
        both = await product_factory.style(style_code=f"GI{tag}B", main_image_key=f"k/{tag}b")
        both.external_image_url = EXT.format("b")
        bare = await product_factory.style(style_code=f"GI{tag}N")
        bare2 = await product_factory.style(style_code=f"GI{tag}M")
        await session.flush()

        # 套装：一个有图一个没有 → 只一张
        half = await _goods(session, tenant_a, f"GIH{tag}", [bare, up])
        # 套装：都有 → 按成员顺序两张（外部链接那张在前；上传主图优先于外部链接）
        full = await _goods(session, tenant_a, f"GIF{tag}", [ext, both])
        # 都没有 → []
        none = await _goods(session, tenant_a, f"GIN{tag}", [bare, bare2])
        # goods_main.main_image_key 有值、成员都没图 → 仍为 []（已废弃，不再读）
        stale = await _goods(session, tenant_a, f"GIS{tag}", [bare2], main_image_key=f"stale/{tag}")
        # 停用的成员不算
        off = await _goods(session, tenant_a, f"GIO{tag}", [up, ext], inactive=frozenset({0}))
        # 单品：最多 1 张
        single = await _goods(session, tenant_a, f"GI1{tag}", [up])

        expected = {
            half.id: [(up.style_code, _signed(f"k/{tag}u"), "upload")],
            full.id: [
                (ext.style_code, EXT.format(tag), "external"),
                (both.style_code, _signed(f"k/{tag}b"), "upload"),
            ],
            none.id: [],
            stale.id: [],
            off.id: [(ext.style_code, EXT.format(tag), "external")],
            single.id: [(up.style_code, _signed(f"k/{tag}u"), "upload")],
        }

        svc = GoodsService(session)
        rows, total = await svc.list_goods(
            tenant_id=tenant_a.id, filters=GoodsListFilters(keyword=tag), page=1, page_size=50
        )
        assert total == len(expected)
        assert {r.id: _pairs(r.images) for r in rows} == expected
        for goods_id, images in expected.items():
            detail = await svc.get(goods_id)
            assert _pairs(detail.images) == images
            assert "main_image_key" not in detail.model_dump()


# ---------------------------------------------------------------------------
# 投产：实时与汇总两条读路径
# ---------------------------------------------------------------------------


def _prev_month(today: date) -> tuple[date, date]:
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


M_LO, M_HI = _prev_month(get_today())


class _SourceRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, report: str, source: str) -> None:
        self.calls.append((report, source))


async def _linked(session: AsyncSession, tenant: Any, style: Any, goods: GoodsMain) -> None:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform="千牛",
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
        goods_main_id=goods.id,
    )
    session.add(pp)
    await session.flush()
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=M_LO,
            visitors=10,
            pay_amount=Decimal("100.00"),
            pay_orders=1,
        )
    )
    await session.flush()


class TestProductionMainImage:
    async def test_member_style_only_both_paths(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        tenant_ctx: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _SourceRecorder()
        monkeypatch.setattr(production_mod, "record_source", recorder)
        tag = uuid4().hex[:6]
        with_img = await product_factory.style(style_code=f"PI{tag}A", main_image_key=f"s/{tag}a")
        ext_only = await product_factory.style(style_code=f"PI{tag}B")
        ext_only.external_image_url = EXT.format(tag)
        # 商品自己的 main_image_key（037 / 041 复制来的旧 key）不再优先
        g1 = await _goods(
            session, tenant_a, f"PIG1{tag}", [with_img], main_image_key=f"stale/{tag}1"
        )
        # 成员只有外部链接：投产仍只用已上传主图 → 没有图（外部链接兜底不在本轮）
        g2 = await _goods(
            session, tenant_a, f"PIG2{tag}", [ext_only], main_image_key=f"stale/{tag}2"
        )
        await _linked(session, tenant_a, with_img, g1)
        await _linked(session, tenant_a, ext_only, g2)
        await session.commit()
        await SummaryRefreshService(session).refresh(
            tenant_id=tenant_a.id, date_from=M_LO, date_to=M_HI
        )
        await session.commit()

        svc = ProductionService(session)
        via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI))
        assert ("production", "summary") in recorder.calls
        via_live = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
        for report in (via_summary, via_live):
            urls = {r.goods_id: r.main_image_url for r in report.items}
            assert urls[g1.id] == _signed(f"s/{tag}a")
            assert urls[g2.id] is None
