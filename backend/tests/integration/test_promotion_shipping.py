"""流程线 PR-2：推广单收件 / 发货（设计 3.3、4.6、7.3）。

本文件按条目逐步长：S5 收件三项（字段规则投影、写权限、电话规范化、``source_extra`` 退役键拒收）；
S6 商品明细（``POST /`` 的 ``items`` 校验、响应 ``items`` / ``legacy_color_spec``、列表一次批量查、SKU 引用计数）。
角色一律用 ``flow_users``（迁移 seed 的真实角色）。断言落库值时直接 SELECT，不看响应。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.service import SkuService
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import (
    FieldPermissionDenied,
    InvalidReceiverPhoneError,
    InvalidSkuReferenceError,
    PromotionNotFoundError,
    SourceExtraKeyRetiredError,
)
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionCreate,
    PromotionListFilters,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13812345678",
    "receiver_address": "浙江省杭州市西湖区某路 1 号",
}


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _override(session: AsyncSession, tenant: Any, user: Any, scope: str, effect: str) -> None:
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=perm.id, effect=effect
        )
    )
    await session.flush()


async def _db_receiver(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            sa_text(
                "SELECT receiver_name, receiver_phone, receiver_address, source_extra "
                "FROM promotion WHERE id = :pid"
            ),
            {"pid": promotion_id},
        )
    ).one()
    return {
        "receiver_name": row[0],
        "receiver_phone": row[1],
        "receiver_address": row[2],
        "source_extra": dict(row[3]),
    }


async def _promotion(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any, **kw: Any
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr, **kw)


def _receiver_of(resp: Any) -> dict[str, Any]:
    return {k: getattr(resp, k) for k in _RECEIVER}


# ---------------------------------------------------------------------------
# 读：字段规则 promotion.receiver_*（可见 admin / pr / pr_manager / warehouse）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverRead:
    async def test_detail_projection_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)
        for who in ("pr", "pr_manager", "admin", "warehouse"):
            resp = await svc.get_promotion(promo.id, getattr(flow_users, who))
            assert _receiver_of(resp) == _RECEIVER, who
        # 财务看不到收货地址（Q4），运营看不到收件（4.6）
        for who in ("finance", "operations"):
            resp = await svc.get_promotion(promo.id, getattr(flow_users, who))
            assert _receiver_of(resp) == dict.fromkeys(_RECEIVER), who

    async def test_list_projection_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)

        async def _row(user: Any) -> Any:
            page = await svc.list_promotions(
                filters=PromotionListFilters(), page=1, page_size=100, user=user
            )
            return next(p for p in page.items if p.id == promo.id)

        assert _receiver_of(await _row(flow_users.pr_manager)) == _RECEIVER
        assert _receiver_of(await _row(flow_users.operations)) == dict.fromkeys(_RECEIVER)

    async def test_read_revoke_hides_only_that_field(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        await _override(
            session,
            flow_users.tenant,
            flow_users.pr,
            "field.promotion.receiver_phone:read",
            "revoke",
        )
        await _override(
            session,
            flow_users.tenant,
            flow_users.operations,
            "field.promotion.receiver_address:read",
            "grant",
        )
        svc = PromotionService(session)
        pr_view = await svc.get_promotion(promo.id, flow_users.pr)
        assert pr_view.receiver_phone is None
        assert pr_view.receiver_name == _RECEIVER["receiver_name"]
        ops_view = await svc.get_promotion(promo.id, flow_users.operations)
        assert ops_view.receiver_address == _RECEIVER["receiver_address"]
        assert ops_view.receiver_name is None


# ---------------------------------------------------------------------------
# 写：PATCH / POST 的收件三项
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverWrite:
    async def test_pr_and_manager_can_patch_and_phone_is_normalized(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        svc = PromotionService(session)

        resp = await svc.update_promotion(
            promo.id,
            PromotionUpdate(
                receiver_name="  李四 ",
                receiver_phone="+86 138-1234-5678",
                receiver_address=" 上海市某路 2 号 ",
            ),
            flow_users.pr,
        )
        expected = {
            "receiver_name": "李四",
            "receiver_phone": "13812345678",
            "receiver_address": "上海市某路 2 号",
        }
        assert _receiver_of(resp) == expected
        db = await _db_receiver(session, promo.id)
        assert {k: db[k] for k in _RECEIVER} == expected

        # 主管只改地址：没传的两项不动
        await svc.update_promotion(
            promo.id, PromotionUpdate(receiver_address="北京市某路 3 号"), flow_users.pr_manager
        )
        db = await _db_receiver(session, promo.id)
        assert db["receiver_address"] == "北京市某路 3 号"
        assert db["receiver_name"] == "李四"
        assert db["receiver_phone"] == "13812345678"

    async def test_blank_or_null_clears(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)
        await svc.update_promotion(
            promo.id,
            PromotionUpdate(receiver_phone="   ", receiver_name=None),
            flow_users.pr,
        )
        db = await _db_receiver(session, promo.id)
        assert db["receiver_phone"] is None
        assert db["receiver_name"] is None
        assert db["receiver_address"] == _RECEIVER["receiver_address"]

    async def test_invalid_phone_rejected_and_nothing_written(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        pid = promo.id
        with pytest.raises(InvalidReceiverPhoneError) as exc_info:
            await PromotionService(session).update_promotion(
                pid,
                PromotionUpdate(receiver_phone="1381234567", receiver_name="王五"),
                flow_users.pr,
            )
        assert exc_info.value.code == "INVALID_RECEIVER_PHONE"
        # 不 rollback（会连造的数一起回滚）：SELECT 前的 autoflush 会把改了一半的属性写下去
        db = await _db_receiver(session, pid)
        assert {k: db[k] for k in _RECEIVER} == _RECEIVER

    async def test_write_revoke_is_denied_with_all_fields(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        for field in ("receiver_name", "receiver_address"):
            await _override(
                session,
                flow_users.tenant,
                flow_users.pr,
                f"field.promotion.{field}:write",
                "revoke",
            )
        with pytest.raises(FieldPermissionDenied) as exc_info:
            await PromotionService(session).update_promotion(
                promo.id,
                PromotionUpdate(**_RECEIVER),
                flow_users.pr,
            )
        assert exc_info.value.fields == ("receiver_name", "receiver_address")

    async def test_create_with_receiver(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        svc = PromotionService(session)
        resp = await svc.create_promotion(
            PromotionCreate(
                cooperation_mode=CooperationMode.GIFT,
                style_id=style.id,
                blogger_id=blogger.id,
                platform="小红书",
                receiver_name="赵六",
                receiver_phone="0571-8888888",
                receiver_address="杭州市某路 4 号",
            ),
            flow_users.pr_manager,
        )
        db = await _db_receiver(session, resp.id)
        assert db["receiver_name"] == "赵六"
        assert db["receiver_phone"] == "05718888888"
        assert db["receiver_address"] == "杭州市某路 4 号"

        with pytest.raises(InvalidReceiverPhoneError):
            await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    receiver_phone="12345",
                ),
                flow_users.pr_manager,
            )


# ---------------------------------------------------------------------------
# source_extra 退役键：「打单地址」「发货单号」M1 后是 typed 列（7.1 SOURCE_EXTRA_KEY_RETIRED）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestRetiredSourceExtraKeys:
    @pytest.mark.parametrize(
        ("patch", "keys"),
        [
            ({"打单地址": "杭州"}, ["打单地址"]),
            ({"发货单号": "SF1", "订单号": "TB1"}, ["发货单号"]),
            # 删键也不行：键本身退役了
            ({"打单地址": None, "发货单号": ""}, ["打单地址", "发货单号"]),
        ],
    )
    async def test_patch_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        patch: dict[str, Any],
        keys: list[str],
    ) -> None:
        promo = await _promotion(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            source_extra={"订单号": "TB0"},
        )
        pid = promo.id
        with pytest.raises(SourceExtraKeyRetiredError) as exc_info:
            await PromotionService(session).update_promotion(
                pid, PromotionUpdate(source_extra=patch), flow_users.pr
            )
        err = exc_info.value
        assert (err.code, err.status_code, err.details) == (
            "SOURCE_EXTRA_KEY_RETIRED",
            422,
            {"keys": keys},
        )
        assert (await _db_receiver(session, pid))["source_extra"] == {"订单号": "TB0"}

    async def test_patch_checks_retired_before_field_permission_and_phone(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """顺序（细化 §7）：404 → 退役键 → 写权限 → 其余校验。"""
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        await _override(
            session,
            flow_users.tenant,
            flow_users.pr,
            "field.promotion.receiver_name:write",
            "revoke",
        )
        svc = PromotionService(session)
        payload = PromotionUpdate(
            source_extra={"打单地址": "杭州"}, receiver_name="甲", receiver_phone="x"
        )
        with pytest.raises(SourceExtraKeyRetiredError):
            await svc.update_promotion(promo.id, payload, flow_users.pr)
        with pytest.raises(PromotionNotFoundError):
            await svc.update_promotion(uuid4(), payload, flow_users.pr)
        # 权限先于电话格式
        with pytest.raises(FieldPermissionDenied):
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(receiver_name="甲", receiver_phone="x"),
                flow_users.pr,
            )

    async def test_create_rejected_first(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
    ) -> None:
        """POST 最先判：款式不存在也先报退役键。"""
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        with pytest.raises(SourceExtraKeyRetiredError) as exc_info:
            await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=uuid4(),
                    blogger_id=blogger.id,
                    platform="小红书",
                    source_extra={"订单号": "TB1", "发货单号": "SF1"},
                ),
                flow_users.pr_manager,
            )
        assert exc_info.value.details == {"keys": ["发货单号"]}


# ---------------------------------------------------------------------------
# 商品明细：POST / 的 items（设计 2.2、7.3；款式集合 = 归属商品的启用成员）
# ---------------------------------------------------------------------------


async def _goods(
    session: AsyncSession, tenant: Any, *styles: Any, is_suit: bool, active: bool = True
) -> GoodsMain:
    code = f"G{uuid4().hex[:8]}"
    goods = GoodsMain(tenant_id=tenant.id, goods_code=code, goods_title=code, is_suit=is_suit)
    session.add(goods)
    await session.flush()
    for order, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id,
                goods_main_id=goods.id,
                style_id=style.id,
                sort_order=order,
                is_active=active,
            )
        )
    await session.flush()
    return goods


async def _db_items(session: AsyncSession, promotion_id: UUID) -> list[tuple[UUID, UUID, int]]:
    rows = await session.execute(
        sa_text(
            "SELECT style_id, sku_id, sort_order FROM promotion_item "
            "WHERE promotion_id = :pid ORDER BY sort_order"
        ),
        {"pid": promotion_id},
    )
    return [(r[0], r[1], r[2]) for r in rows.all()]


class _Suit:
    """套装（上衣 + 裤子）各一个 SKU，外加一个不相干的款式 C。"""

    def __init__(self) -> None:
        self.top: Any = None
        self.pants: Any = None
        self.other: Any = None
        self.top_sku: Any = None
        self.pants_sku: Any = None
        self.other_sku: Any = None
        self.goods: Any = None


@pytest.fixture
async def suit(session: AsyncSession, tenant_a: Any, product_factory: Any) -> _Suit:
    s = _Suit()
    s.top = await product_factory.style(short_name="上衣", style_name="条纹上衣全称")
    s.pants = await product_factory.style(short_name=None, style_name="阔腿裤")
    s.other = await product_factory.style()
    s.top_sku = await product_factory.sku(s.top, color="黑色", size="M")
    s.pants_sku = await product_factory.sku(s.pants, color="白色", size="L")
    s.other_sku = await product_factory.sku(s.other)
    s.goods = await _goods(session, tenant_a, s.top, s.pants, is_suit=True)
    return s


def _create(
    style_id: UUID, blogger_id: UUID, *, goods_main_id: UUID | None = None, **kw: Any
) -> PromotionCreate:
    return PromotionCreate(
        cooperation_mode=CooperationMode.GIFT,
        style_id=style_id,
        goods_main_id=goods_main_id,
        blogger_id=blogger_id,
        platform="小红书",
        **kw,
    )


@pytest.mark.usefixtures("tenant_ctx")
class TestCreateItems:
    async def test_suit_writes_one_row_per_member(
        self, session: AsyncSession, flow_users: Any, blogger_factory: Any, suit: _Suit
    ) -> None:
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        resp = await PromotionService(session).create_promotion(
            _create(
                suit.top.id,
                blogger.id,
                goods_main_id=suit.goods.id,
                items=[
                    GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id),
                    GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id),
                ],
            ),
            flow_users.pr_manager,
        )
        # 按传入顺序落 sort_order；promotion.sku_id = 主款式那一行
        assert await _db_items(session, resp.id) == [
            (suit.pants.id, suit.pants_sku.id, 0),
            (suit.top.id, suit.top_sku.id, 1),
        ]
        sku_id = (
            await session.execute(
                sa_text("SELECT sku_id FROM promotion WHERE id = :pid"), {"pid": resp.id}
            )
        ).scalar_one()
        assert sku_id == suit.top_sku.id
        assert resp.sku_id == suit.top_sku.id
        assert [
            (i.style_id, i.sku_id, i.color, i.size, i.display_short_name, i.goods_title)
            for i in resp.items
        ] == [
            (suit.pants.id, suit.pants_sku.id, "白色", "L", "阔腿裤", "阔腿裤"),
            (suit.top.id, suit.top_sku.id, "黑色", "M", "上衣", "条纹上衣全称"),
        ]
        assert resp.legacy_color_spec is None

    async def test_single_without_goods_is_one_row(
        self, session: AsyncSession, flow_users: Any, blogger_factory: Any, suit: _Suit
    ) -> None:
        """款式不属于任何商品 = 单品 1 行（成员 = [style_id]）。"""
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        resp = await PromotionService(session).create_promotion(
            _create(
                suit.other.id,
                blogger.id,
                items=[GoodsItemIn(style_id=suit.other.id, sku_id=suit.other_sku.id)],
            ),
            flow_users.pr_manager,
        )
        assert resp.goods_main_id is None
        assert await _db_items(session, resp.id) == [(suit.other.id, suit.other_sku.id, 0)]

    async def test_without_items_writes_nothing_and_falls_back_to_legacy_spec(
        self, session: AsyncSession, flow_users: Any, blogger_factory: Any, suit: _Suit
    ) -> None:
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        resp = await PromotionService(session).create_promotion(
            _create(
                suit.top.id,
                blogger.id,
                goods_main_id=suit.goods.id,
                source_extra={"颜色及规格": "黑色 M"},
            ),
            flow_users.pr_manager,
        )
        assert await _db_items(session, resp.id) == []
        assert resp.items == []
        assert resp.legacy_color_spec == "黑色 M"

    @pytest.mark.parametrize(
        ("rows", "details"),
        [
            # 少一个成员
            (("top",), {"missing_style_ids": ["pants"], "extra_style_ids": []}),
            # 多一个不相干的款式
            (
                ("top", "pants", "other"),
                {"missing_style_ids": [], "extra_style_ids": ["other"]},
            ),
            # 空数组 = 传了，成员全缺
            ((), {"missing_style_ids": ["top", "pants"], "extra_style_ids": []}),
        ],
    )
    async def test_member_set_must_match(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        suit: _Suit,
        rows: tuple[str, ...],
        details: dict[str, list[str]],
    ) -> None:
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        items = [
            GoodsItemIn(style_id=getattr(suit, n).id, sku_id=getattr(suit, f"{n}_sku").id)
            for n in rows
        ]
        with pytest.raises(ValidationError) as exc_info:
            await PromotionService(session).create_promotion(
                _create(suit.top.id, blogger.id, goods_main_id=suit.goods.id, items=items),
                flow_users.pr_manager,
            )
        err = exc_info.value
        assert (err.code, err.status_code) == ("VALIDATION_ERROR", 422)
        assert err.details == {k: [str(getattr(suit, n).id) for n in v] for k, v in details.items()}
        count = (await session.execute(sa_text("SELECT COUNT(*) FROM promotion_item"))).scalar_one()
        assert count == 0

    async def test_duplicate_style_and_missing_sku_rejected(
        self, session: AsyncSession, flow_users: Any, blogger_factory: Any, suit: _Suit
    ) -> None:
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        svc = PromotionService(session)
        top = GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id)
        pants = GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id)
        with pytest.raises(ValidationError) as dup:
            await svc.create_promotion(
                _create(
                    suit.top.id, blogger.id, goods_main_id=suit.goods.id, items=[top, top, pants]
                ),
                flow_users.pr_manager,
            )
        assert dup.value.code == "VALIDATION_ERROR"
        assert dup.value.details == {"duplicate_style_ids": [str(suit.top.id)]}

        with pytest.raises(ValidationError) as no_sku:
            await svc.create_promotion(
                _create(
                    suit.top.id,
                    blogger.id,
                    goods_main_id=suit.goods.id,
                    items=[top, GoodsItemIn(style_id=suit.pants.id)],
                ),
                flow_users.pr_manager,
            )
        assert no_sku.value.code == "VALIDATION_ERROR"
        assert no_sku.value.details == {"missing_sku_style_ids": [str(suit.pants.id)]}

    async def test_sku_must_belong_to_row_style_and_not_deleted(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        product_factory: Any,
        suit: _Suit,
    ) -> None:
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        svc = PromotionService(session)
        top = GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id)
        # 上衣那行填了裤子的 SKU
        with pytest.raises(InvalidSkuReferenceError) as wrong:
            await svc.create_promotion(
                _create(
                    suit.top.id,
                    blogger.id,
                    goods_main_id=suit.goods.id,
                    items=[
                        GoodsItemIn(style_id=suit.top.id, sku_id=suit.pants_sku.id),
                        GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id),
                    ],
                ),
                flow_users.pr_manager,
            )
        assert (wrong.value.code, wrong.value.status_code) == ("INVALID_SKU_REFERENCE", 422)
        assert wrong.value.details == {
            "style_id": str(suit.top.id),
            "sku_id": str(suit.pants_sku.id),
        }

        deleted = await product_factory.sku(suit.pants, is_deleted=True, is_active=False)
        with pytest.raises(InvalidSkuReferenceError):
            await svc.create_promotion(
                _create(
                    suit.top.id,
                    blogger.id,
                    goods_main_id=suit.goods.id,
                    items=[top, GoodsItemIn(style_id=suit.pants.id, sku_id=deleted.id)],
                ),
                flow_users.pr_manager,
            )

    async def test_sku_id_conflicting_with_main_row_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        product_factory: Any,
        suit: _Suit,
    ) -> None:
        """``sku_id`` 与明细主款式那行不一致 → 422（``promotion.sku_id`` 只能是主款式那行）。"""
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        another_top_sku = await product_factory.sku(suit.top, color="灰色", size="S")
        with pytest.raises(InvalidSkuReferenceError):
            await PromotionService(session).create_promotion(
                _create(
                    suit.top.id,
                    blogger.id,
                    goods_main_id=suit.goods.id,
                    sku_id=another_top_sku.id,
                    items=[
                        GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id),
                        GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id),
                    ],
                ),
                flow_users.pr_manager,
            )


# ---------------------------------------------------------------------------
# 读：响应 items / legacy_color_spec；列表一页只发一条明细查询
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReadItems:
    async def test_detail_items_in_sort_order(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            source_extra={"颜色及规格": "旧写法"},
            items=[(suit.top, suit.top_sku), (suit.pants, suit.pants_sku)],
        )
        resp = await PromotionService(session).get_promotion(promo.id, flow_users.pr)
        assert [(i.style_id, i.color, i.size) for i in resp.items] == [
            (suit.top.id, "黑色", "M"),
            (suit.pants.id, "白色", "L"),
        ]
        # 有明细时不回落旧文字
        assert resp.legacy_color_spec is None

    async def test_list_batches_items_in_one_query(
        self,
        session: AsyncSession,
        engine: Any,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        blogger = await blogger_factory.blogger()
        with_items = [
            await promotion_factory.promotion(
                style=suit.top,
                blogger=blogger,
                pr=flow_users.pr,
                goods_main_id=suit.goods.id,
                items=[(suit.top, suit.top_sku), (suit.pants, suit.pants_sku)],
            )
            for _ in range(3)
        ]
        legacy = await promotion_factory.promotion(
            style=suit.other,
            blogger=blogger,
            pr=flow_users.pr,
            source_extra={"颜色及规格": "黑色 M"},
        )
        statements: list[str] = []

        def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _capture)
        try:
            page = await PromotionService(session).list_promotions(
                filters=PromotionListFilters(), page=1, page_size=100, user=flow_users.pr
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _capture)

        assert sum("promotion_item" in s for s in statements) == 1
        by_id = {p.id: p for p in page.items}
        for promo in with_items:
            row = by_id[promo.id]
            assert [(i.style_id, i.sku_id) for i in row.items] == [
                (suit.top.id, suit.top_sku.id),
                (suit.pants.id, suit.pants_sku.id),
            ]
            assert row.legacy_color_spec is None
        assert by_id[legacy.id].items == []
        assert by_id[legacy.id].legacy_color_spec == "黑色 M"


# ---------------------------------------------------------------------------
# SKU 删除前的引用计数：promotion.sku_id 与 promotion_item.sku_id 按推广单去重（7.8）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestSkuReferences:
    async def test_check_references_counts_distinct_promotions(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        blogger = await blogger_factory.blogger()
        top, sku = suit.top, suit.top_sku
        # 两处都引用（只算 1）、只在明细里、只在 promotion.sku_id 上
        await promotion_factory.promotion(
            style=top, blogger=blogger, pr=flow_users.pr, sku_id=sku.id, items=[(top, sku)]
        )
        await promotion_factory.promotion(
            style=top, blogger=blogger, pr=flow_users.pr, items=[(top, sku)]
        )
        await promotion_factory.promotion(
            style=top, blogger=blogger, pr=flow_users.pr, sku_id=sku.id
        )
        refs = await SkuService(session).check_references(sku.id)
        assert refs == {"promotion_count": 3, "order_count": 0}
        # 只被套装另一款的明细引用
        refs = await SkuService(session).check_references(suit.pants_sku.id)
        assert refs == {"promotion_count": 0, "order_count": 0}
        await promotion_factory.promotion(
            style=top,
            blogger=blogger,
            pr=flow_users.pr,
            items=[(top, sku), (suit.pants, suit.pants_sku)],
        )
        refs = await SkuService(session).check_references(suit.pants_sku.id)
        assert refs == {"promotion_count": 1, "order_count": 0}
