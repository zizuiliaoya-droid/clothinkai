"""流程线 PR-2：推广单收件 / 发货（设计 3.3、4.6、7.3）。

本文件按条目逐步长：S5 收件三项（字段规则投影、写权限、电话规范化、``source_extra`` 退役键拒收）；
S6 商品明细（``POST /`` 的 ``items`` 校验、响应 ``items`` / ``legacy_color_spec``、列表一次批量查、SKU 引用计数）；
S7 矩阵 ``ui`` / PATCH / ``PUT items``；S8 发货状态机（纳入 / 推送 / 撤回、取消联动、新建 ``need_shipping``、30 并发推送）。
角色一律用 ``flow_users``（迁移 seed 的真实角色）。断言落库值时直接 SELECT，不看响应。
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AppException,
    IllegalStateTransitionError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, User, UserPermissionOverride
from app.modules.flow.exceptions import FlowGateMissingError
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.service import SkuService
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import (
    FieldPermissionDenied,
    InvalidReceiverPhoneError,
    InvalidSkuReferenceError,
    PromotionNotFoundError,
    SourceExtraKeyRetiredError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionCancelRequest,
    PromotionCreate,
    PromotionListFilters,
    PromotionShipPushRequest,
    PromotionShipWithdrawRequest,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService
from tests.concurrency import committed, default_tenant_id, run_concurrently
from tests.conftest import purge_promotions

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


# ---------------------------------------------------------------------------
# 矩阵接上响应：ui（列表 actions + edits，详情 actions + fields；设计 7.1）
# ---------------------------------------------------------------------------

_ALL_GATES = [
    {"key": "receiver_name", "label": "收件人"},
    {"key": "receiver_phone", "label": "收件电话"},
    {"key": "receiver_address", "label": "收件地址"},
    {"key": "goods_items", "label": "颜色尺码"},
]


@pytest.mark.usefixtures("tenant_ctx")
class TestFlowUi:
    async def test_detail_ui_ship_push_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待发货"
        )
        svc = PromotionService(session)
        pr = (await svc.get_promotion(promo.id, flow_users.pr)).ui
        assert pr == {
            "column": "A",
            "actions": {"ship_push": {"state": "disabled", "reason": "需管理员或 PR 主管确认"}},
            "fields": {
                "goods_items": {"state": "read"},
                "receiver": {"state": "edit"},
                "shipping": {"state": "grey", "hint": "推送后由仓库回填"},
            },
        }
        # 主管：可点，缺项全是弹窗里能补的
        manager = (await svc.get_promotion(promo.id, flow_users.pr_manager)).ui
        assert manager is not None
        assert manager["actions"] == {"ship_push": {"state": "enabled", "missing": _ALL_GATES}}
        # 运营只读、财务什么都没有
        ops = (await svc.get_promotion(promo.id, flow_users.operations)).ui
        assert ops is not None
        assert ops["actions"] == {}
        assert ops["fields"] == {
            "goods_items": {"state": "read"},
            "shipping": {"state": "grey", "hint": "推送后由仓库回填"},
        }
        assert (await svc.get_promotion(promo.id, flow_users.finance)).ui == {
            "column": "A",
            "actions": {},
            "fields": {},
        }

    async def test_missing_follows_receiver_and_suit_members(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """缺电话（格式不对）+ 套装少一个成员 → missing 恰两项；补齐后没有 missing。"""
        blogger = await blogger_factory.blogger()
        partial = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
            receiver_name="张三",
            receiver_phone="12345",
            receiver_address="杭州",
            items=[(suit.top, suit.top_sku)],
        )
        full = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
            **_RECEIVER,
            items=[(suit.pants, suit.pants_sku), (suit.top, suit.top_sku)],
        )
        svc = PromotionService(session)
        got = (await svc.get_promotion(partial.id, flow_users.pr_manager)).ui
        assert got is not None
        assert got["actions"]["ship_push"] == {
            "state": "enabled",
            "missing": [
                {"key": "receiver_phone", "label": "收件电话"},
                {"key": "goods_items", "label": "颜色尺码"},
            ],
        }
        # 列表与详情同一份结论（列表的成员整页一次查）
        page = await svc.list_promotions(
            filters=PromotionListFilters(), page=1, page_size=100, user=flow_users.pr_manager
        )
        rows = {p.id: p.ui for p in page.items}
        assert rows[partial.id] is not None and rows[full.id] is not None
        assert rows[partial.id]["actions"] == got["actions"]
        assert rows[full.id]["actions"] == {"ship_push": {"state": "enabled"}}

    async def test_list_ui_has_edits_not_fields(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        history = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        shipped = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="已发货"
        )
        page = await PromotionService(session).list_promotions(
            filters=PromotionListFilters(), page=1, page_size=100, user=flow_users.pr
        )
        rows = {p.id: p.ui for p in page.items}
        # 历史单（发货为空）按 A 列：PR 能改收件；已发货的 C 列只读
        assert rows[history.id] == {"column": "C", "actions": {}, "edits": ["receiver"]}
        assert rows[shipped.id] == {"column": "C", "actions": {}, "edits": []}
        manager = await PromotionService(session).list_promotions(
            filters=PromotionListFilters(), page=1, page_size=100, user=flow_users.pr_manager
        )
        row = next(p for p in manager.items if p.id == history.id)
        assert row.ui == {
            "column": "C",
            "actions": {"ship_include": {"state": "enabled"}},
            "edits": ["goods_items", "receiver"],
        }

    async def test_negotiator_from_negotiation_else_pr(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """快照的谈款人：有谈款取谈款的 PR，没有回落负责 PR（L1，整页一次查）。"""
        from app.modules.negotiation.models import Negotiation
        from app.modules.promotion.repository import PromotionRepository

        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        negotiated = await promotion_factory.promotion(
            style=style, blogger=blogger, pr=flow_users.pr
        )
        imported = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        session.add(
            Negotiation(
                tenant_id=flow_users.tenant.id,
                blogger_id=blogger.id,
                style_id=style.id,
                pr_id=flow_users.pr2.id,
                promotion_id=negotiated.id,
                cooperation_mode="送拍",
                status="审核通过",
            )
        )
        await session.flush()
        got = await PromotionRepository(session).negotiator_ids([negotiated.id, imported.id])
        assert got == {negotiated.id: flow_users.pr2.id}


# ---------------------------------------------------------------------------
# PATCH：已入矩阵的字段（收件三项 → receiver，sku_id → goods_items）按当前格判（5.4 过渡规则）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestPatchByMatrix:
    async def test_pr_patch_managed_fields_denied_with_all_fields(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        blogger = await blogger_factory.blogger()
        history = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        shipped = await promotion_factory.promotion(
            style=style, blogger=blogger, pr=flow_users.pr, ship_status="已发货"
        )
        svc = PromotionService(session)
        # 历史单：PR 颜色尺码「读」
        with pytest.raises(FieldPermissionDenied) as exc_info:
            await svc.update_promotion(history.id, PromotionUpdate(sku_id=sku.id), flow_users.pr)
        assert exc_info.value.code == "FIELD_PERMISSION_DENIED"
        assert exc_info.value.fields == ("sku_id",)
        # 已发货：收件与颜色尺码都只读，一次全列出来（按 patch_groups 登记顺序）
        with pytest.raises(FieldPermissionDenied) as exc_info:
            await svc.update_promotion(
                shipped.id,
                PromotionUpdate(sku_id=sku.id, receiver_name="甲", note_title="标题"),
                flow_users.pr,
            )
        assert exc_info.value.details["fields"] == ["receiver_name", "sku_id"]
        # 没入矩阵的字段照旧可写
        resp = await svc.update_promotion(
            shipped.id, PromotionUpdate(note_title="新标题"), flow_users.pr
        )
        assert resp.note_title == "新标题"
        # 管理员已发货后照样能改
        await svc.update_promotion(
            shipped.id, PromotionUpdate(receiver_name="乙", sku_id=sku.id), flow_users.admin
        )
        assert (await _db_receiver(session, shipped.id))["receiver_name"] == "乙"

    async def test_retired_key_checked_before_matrix(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="已发货"
        )
        sku = await product_factory.sku(await product_factory.style())
        with pytest.raises(SourceExtraKeyRetiredError):
            await PromotionService(session).update_promotion(
                promo.id,
                PromotionUpdate(source_extra={"打单地址": "x"}, sku_id=sku.id),
                flow_users.pr,
            )

    async def test_patch_sku_syncs_main_item_row(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        product_factory: Any,
        suit: _Suit,
    ) -> None:
        """PATCH sku_id 同步明细里主款式那一行：有就改、没有就插。"""
        blogger = await blogger_factory.blogger()
        new_top_sku = await product_factory.sku(suit.top, color="灰色", size="S")
        with_items = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
            sku_id=suit.top_sku.id,
            items=[(suit.pants, suit.pants_sku), (suit.top, suit.top_sku)],
        )
        without_items = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
        )
        svc = PromotionService(session)
        await svc.update_promotion(
            with_items.id, PromotionUpdate(sku_id=new_top_sku.id), flow_users.pr_manager
        )
        assert await _db_items(session, with_items.id) == [
            (suit.pants.id, suit.pants_sku.id, 0),
            (suit.top.id, new_top_sku.id, 1),
        ]
        resp = await svc.update_promotion(
            without_items.id, PromotionUpdate(sku_id=new_top_sku.id), flow_users.pr_manager
        )
        assert await _db_items(session, without_items.id) == [(suit.top.id, new_top_sku.id, 0)]
        assert [(i.style_id, i.sku_id) for i in resp.items] == [(suit.top.id, new_top_sku.id)]


# ---------------------------------------------------------------------------
# PUT /{id}/items：整组替换颜色尺码明细（7.3；矩阵 goods_items 不是「改」→ 403）
# ---------------------------------------------------------------------------


def _items(*pairs: tuple[Any, Any]) -> list[GoodsItemIn]:
    return [GoodsItemIn(style_id=style.id, sku_id=sku.id if sku else None) for style, sku in pairs]


@pytest.mark.usefixtures("tenant_ctx")
class TestReplaceItems:
    async def _suit_promotion(
        self, promotion_factory: Any, blogger_factory: Any, flow_users: Any, suit: _Suit, **kw: Any
    ) -> Any:
        blogger = await blogger_factory.blogger()
        return await promotion_factory.promotion(
            style=suit.top, blogger=blogger, pr=flow_users.pr, goods_main_id=suit.goods.id, **kw
        )

    async def test_manager_replaces_and_syncs_sku(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        promo = await self._suit_promotion(
            promotion_factory, blogger_factory, flow_users, suit, ship_status="待发货"
        )
        resp = await PromotionService(session).replace_items(
            promo.id,
            _items((suit.pants, suit.pants_sku), (suit.top, suit.top_sku)),
            flow_users.pr_manager,
        )
        assert await _db_items(session, promo.id) == [
            (suit.pants.id, suit.pants_sku.id, 0),
            (suit.top.id, suit.top_sku.id, 1),
        ]
        sku_id = (
            await session.execute(
                sa_text("SELECT sku_id FROM promotion WHERE id = :pid"), {"pid": promo.id}
            )
        ).scalar_one()
        assert sku_id == suit.top_sku.id
        assert resp.sku_id == suit.top_sku.id
        assert [(i.style_id, i.color, i.size) for i in resp.items] == [
            (suit.pants.id, "白色", "L"),
            (suit.top.id, "黑色", "M"),
        ]
        # 明细齐了，推送不再缺颜色尺码
        assert resp.ui is not None
        assert {"key": "goods_items", "label": "颜色尺码"} not in resp.ui["actions"]["ship_push"][
            "missing"
        ]

    @pytest.mark.parametrize(
        ("ship_status", "who", "allowed"),
        [
            ("待发货", "pr", False),  # A 列 PR 读
            (None, "pr", False),  # 历史单按 A 列
            (None, "pr_manager", True),
            ("待打单", "pr_manager", False),  # 推送后只有管理员
            ("待打单", "admin", True),
            ("已发货", "admin", True),
        ],
    )
    async def test_matrix_decides(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
        ship_status: str | None,
        who: str,
        allowed: bool,
    ) -> None:
        promo = await self._suit_promotion(
            promotion_factory, blogger_factory, flow_users, suit, ship_status=ship_status
        )
        call = PromotionService(session).replace_items(
            promo.id,
            _items((suit.top, suit.top_sku), (suit.pants, suit.pants_sku)),
            getattr(flow_users, who),
        )
        if allowed:
            await call
            assert len(await _db_items(session, promo.id)) == 2
            return
        with pytest.raises(FieldPermissionDenied) as exc_info:
            await call
        assert exc_info.value.code == "FIELD_PERMISSION_DENIED"
        assert exc_info.value.fields == ("goods_items",)
        assert await _db_items(session, promo.id) == []

    async def test_order_404_then_403_then_422(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        promo = await self._suit_promotion(
            promotion_factory, blogger_factory, flow_users, suit, ship_status="待发货"
        )
        svc = PromotionService(session)
        bad = _items((suit.top, suit.top_sku))  # 少一个成员
        with pytest.raises(PromotionNotFoundError):
            await svc.replace_items(uuid4(), bad, flow_users.pr_manager)
        with pytest.raises(FieldPermissionDenied):
            await svc.replace_items(promo.id, bad, flow_users.pr)
        with pytest.raises(ValidationError) as exc_info:
            await svc.replace_items(promo.id, bad, flow_users.pr_manager)
        assert exc_info.value.details == {
            "missing_style_ids": [str(suit.pants.id)],
            "extra_style_ids": [],
        }
        with pytest.raises(ValidationError) as exc_info:
            await svc.replace_items(
                promo.id,
                _items((suit.top, suit.top_sku), (suit.pants, None)),
                flow_users.pr_manager,
            )
        assert exc_info.value.details == {"missing_sku_style_ids": [str(suit.pants.id)]}
        with pytest.raises(InvalidSkuReferenceError):
            await svc.replace_items(
                promo.id,
                _items((suit.top, suit.pants_sku), (suit.pants, suit.pants_sku)),
                flow_users.pr_manager,
            )
        assert await _db_items(session, promo.id) == []

    async def test_route(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """真实路由：``promotion:write`` 在路由层（运营 403 PERMISSION_DENIED），矩阵在 service。"""
        promo = await self._suit_promotion(
            promotion_factory, blogger_factory, flow_users, suit, ship_status="待发货"
        )
        body = [
            {"style_id": str(suit.top.id), "sku_id": str(suit.top_sku.id)},
            {"style_id": str(suit.pants.id), "sku_id": str(suit.pants_sku.id)},
        ]
        path = f"/api/promotions/{promo.id}/items"
        ops = await _call(session, flow_users.operations, "PUT", path, body)
        assert (ops.status_code, ops.json()["code"]) == (403, "PERMISSION_DENIED")
        pr = await _call(session, flow_users.pr, "PUT", path, body)
        assert pr.status_code == 403
        assert pr.json()["code"] == "FIELD_PERMISSION_DENIED"
        assert pr.json()["details"]["fields"] == ["goods_items"]
        ok = await _call(session, flow_users.pr_manager, "PUT", path, body)
        assert ok.status_code == 200, ok.text
        assert [i["style_id"] for i in ok.json()["items"]] == [str(suit.top.id), str(suit.pants.id)]
        assert ok.json()["ui"]["actions"]["ship_push"]["state"] == "enabled"
        too_many = await _call(session, flow_users.pr_manager, "PUT", path, body * 6)
        assert too_many.status_code == 422


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限（照 test_blogger_account_edit._call）。"""
    from collections.abc import AsyncIterator

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


# ---------------------------------------------------------------------------
# S8 发货 3 态（3.3 S1 ~ S4、S7）：service 顺序 404 → 状态机 422 → 规则 403 → 条件 UPDATE 409 → 写补选 → ★ 422
# 推送失败会 rollback：前置数据先 commit、id 先取出来（rollback 后 ORM 对象全部过期）
# ---------------------------------------------------------------------------


async def _db_ship(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                sa_text(
                    "SELECT ship_status, ship_pushed_at, ship_pushed_by, receiver_name, "
                    "receiver_phone, receiver_address, sku_id FROM promotion WHERE id = :pid"
                ),
                {"pid": promotion_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _user_name(session: AsyncSession, user_id: UUID) -> str:
    name: str = (
        await session.execute(
            sa_text('SELECT COALESCE(display_name, username) FROM "user" WHERE id = :u'),
            {"u": user_id},
        )
    ).scalar_one()
    return name


@pytest.mark.usefixtures("tenant_ctx")
class TestShipInclude:
    async def test_manager_includes_history(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        resp = await PromotionService(session).ship_include(promo.id, flow_users.pr_manager)
        assert (await _db_ship(session, promo.id))["ship_status"] == "待发货"
        assert resp.ship_status == "待发货"
        # 进了 A 列：主管接着能推送（缺项都在弹窗里补）
        assert resp.ui is not None
        assert resp.ui["column"] == "A"
        assert resp.ui["actions"]["ship_push"]["state"] == "enabled"

    @pytest.mark.parametrize(
        "kw",
        [
            {"ship_status": "待发货"},
            {"ship_status": "已发货"},
            {"publish_status": "已发布"},
            {"publish_status": "已取消"},
            {"recall_status": "召回中"},
        ],
    )
    async def test_state_machine(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        kw: dict[str, Any],
    ) -> None:
        """（已停用的单 ``get_by_id`` 取不到 → 404，与其他动作一致。）"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **kw
        )
        with pytest.raises(IllegalStateTransitionError) as exc_info:
            await PromotionService(session).ship_include(promo.id, flow_users.pr_manager)
        assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc_info.value.details["action"] == "ship_include"

    async def test_pr_and_404(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        svc = PromotionService(session)
        with pytest.raises(PromotionNotFoundError):
            await svc.ship_include(uuid4(), flow_users.pr_manager)
        with pytest.raises(PermissionDeniedError) as exc_info:
            await svc.ship_include(promo.id, flow_users.pr)
        assert exc_info.value.code == "PERMISSION_DENIED"
        assert (await _db_ship(session, promo.id))["ship_status"] is None


@pytest.mark.usefixtures("tenant_ctx")
class TestShipPush:
    async def test_push_writes_marks_and_moves_to_b(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            sku_id=sku.id,
            ship_status="待发货",
            **_RECEIVER,
            items=[(style, sku)],
        )
        manager = flow_users.pr_manager
        resp = await PromotionService(session).ship_push(
            promo.id, PromotionShipPushRequest(), manager
        )
        db = await _db_ship(session, promo.id)
        assert db["ship_status"] == "待打单"
        assert db["ship_pushed_by"] == manager.id
        assert db["ship_pushed_at"] is not None
        assert resp.ship_status == "待打单"
        assert resp.ship_pushed_at == db["ship_pushed_at"]
        assert resp.ship_pushed_by_name == await _user_name(session, manager.id)
        assert resp.ui is not None
        assert resp.ui["column"] == "B"
        assert resp.ui["actions"] == {"ship_withdraw": {"state": "enabled"}}
        # 列表与详情同一份发货字段（推送人整页一次查）
        page = await PromotionService(session).list_promotions(
            filters=PromotionListFilters(), page=1, page_size=100, user=manager
        )
        row = next(p for p in page.items if p.id == promo.id)
        assert (row.ship_status, row.ship_pushed_by_name) == ("待打单", resp.ship_pushed_by_name)

    async def test_repush_is_state_error_not_missing(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """已推送（待打单）、收件与明细都空的单再推 → 422 状态错，不是缺项（A1）。"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待打单"
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_push(
                promo.id, PromotionShipPushRequest(), flow_users.pr_manager
            )
        assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc_info.value.details == {"from_state": "待仓库发货", "action": "ship_push"}

    @pytest.mark.parametrize(
        "kw",
        [
            {"publish_status": "已发布"},
            {"publish_status": "异常"},
            {"recall_status": "召回中"},
            {"ship_status": "已发货"},
            {"ship_status": None},  # 历史单（发货为空）先走纳入发货
        ],
    )
    async def test_state_machine(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        kw: dict[str, Any],
    ) -> None:
        kw = {"ship_status": "待发货", **kw}
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER, **kw
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_push(
                promo.id, PromotionShipPushRequest(), flow_users.pr_manager
            )
        assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"

    async def test_pr_is_permission_denied(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待发货"
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_push(
                promo.id, PromotionShipPushRequest(), flow_users.pr
            )
        assert exc_info.value.code == "PERMISSION_DENIED"
        assert (await _db_ship(session, promo.id))["ship_status"] == "待发货"

    async def test_missing_exactly_two_and_same_as_ui(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """缺电话（格式不对）+ 套装少一个成员 → 422 缺项恰两项，与 ui 逐条相同；整笔回滚。"""
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
            receiver_name="张三",
            receiver_phone="12345",
            receiver_address="杭州",
            items=[(suit.top, suit.top_sku)],
        )
        svc = PromotionService(session)
        detail = await svc.get_promotion(promo.id, flow_users.pr_manager)
        assert detail.ui is not None
        ui_missing = detail.ui["actions"]["ship_push"]["missing"]
        await session.commit()
        pid, manager = promo.id, flow_users.pr_manager
        with pytest.raises(FlowGateMissingError) as exc_info:
            await svc.ship_push(pid, PromotionShipPushRequest(), manager)
        assert exc_info.value.details["missing"] == ui_missing
        assert ui_missing == [
            {"key": "receiver_phone", "label": "收件电话"},
            {"key": "goods_items", "label": "颜色尺码"},
        ]
        db = await _db_ship(session, pid)
        assert (db["ship_status"], db["ship_pushed_at"], db["ship_pushed_by"]) == (
            "待发货",
            None,
            None,
        )

    async def test_dialog_fill_written_with_push(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """没有明细与收件的单（存量、直接新建）在推送弹窗里补，与推送同一事务。"""
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
        )
        resp = await PromotionService(session).ship_push(
            promo.id,
            PromotionShipPushRequest(
                items=_items((suit.pants, suit.pants_sku), (suit.top, suit.top_sku)),
                receiver_name=" 李四 ",
                receiver_phone="+86 138-1234-5678",
                receiver_address="上海市某路 2 号",
            ),
            flow_users.pr_manager,
        )
        db = await _db_ship(session, promo.id)
        assert db["ship_status"] == "待打单"
        assert (db["receiver_name"], db["receiver_phone"], db["receiver_address"]) == (
            "李四",
            "13812345678",
            "上海市某路 2 号",
        )
        assert db["sku_id"] == suit.top_sku.id
        assert await _db_items(session, promo.id) == [
            (suit.pants.id, suit.pants_sku.id, 0),
            (suit.top.id, suit.top_sku.id, 1),
        ]
        assert [(i.color, i.size) for i in resp.items] == [("白色", "L"), ("黑色", "M")]

    async def test_gate_failure_rolls_back_dialog_fill(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """补了明细、没补收件 → 422 缺收件三项；条件 UPDATE 与补的明细一起回滚。"""
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
        )
        await session.commit()
        pid, manager = promo.id, flow_users.pr_manager
        body = PromotionShipPushRequest(
            items=_items((suit.top, suit.top_sku), (suit.pants, suit.pants_sku))
        )
        with pytest.raises(FlowGateMissingError) as exc_info:
            await PromotionService(session).ship_push(pid, body, manager)
        assert [m["key"] for m in exc_info.value.details["missing"]] == [
            "receiver_name",
            "receiver_phone",
            "receiver_address",
        ]
        db = await _db_ship(session, pid)
        assert (db["ship_status"], db["ship_pushed_at"], db["sku_id"]) == ("待发货", None, None)
        assert await _db_items(session, pid) == []

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ({"receiver_phone": "12345"}, "INVALID_RECEIVER_PHONE"),
            ("wrong_sku", "INVALID_SKU_REFERENCE"),
            ("missing_member", "VALIDATION_ERROR"),
        ],
    )
    async def test_bad_dialog_fill_422(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
        body: Any,
        code: str,
    ) -> None:
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.goods.id,
            ship_status="待发货",
            **_RECEIVER,
        )
        if body == "wrong_sku":  # 上衣那行填了裤子的 SKU
            payload = PromotionShipPushRequest(
                items=_items((suit.top, suit.pants_sku), (suit.pants, suit.pants_sku))
            )
        elif body == "missing_member":
            payload = PromotionShipPushRequest(items=_items((suit.top, suit.top_sku)))
        else:
            payload = PromotionShipPushRequest(
                **body, items=_items((suit.top, suit.top_sku), (suit.pants, suit.pants_sku))
            )
        await session.commit()
        pid, manager = promo.id, flow_users.pr_manager
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_push(pid, payload, manager)
        assert exc_info.value.code == code
        db = await _db_ship(session, pid)
        assert (db["ship_status"], db["receiver_phone"]) == ("待发货", _RECEIVER["receiver_phone"])
        assert await _db_items(session, pid) == []


@pytest.mark.usefixtures("tenant_ctx")
class TestShipWithdraw:
    async def test_manager_withdraws_and_keeps_push_marks(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        from datetime import UTC, datetime

        pushed_at = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
        promo = await _promotion(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            ship_status="待打单",
            ship_pushed_at=pushed_at,
            ship_pushed_by=flow_users.admin.id,
        )
        resp = await PromotionService(session).ship_withdraw(
            promo.id, PromotionShipWithdrawRequest(reason=" 地址写错了 "), flow_users.pr_manager
        )
        db = await _db_ship(session, promo.id)
        # 撤回不清推送时间 / 人（偏差 §2-10：再推覆盖）
        assert (db["ship_status"], db["ship_pushed_at"], db["ship_pushed_by"]) == (
            "待发货",
            pushed_at,
            flow_users.admin.id,
        )
        assert resp.ship_status == "待发货"
        audit = (
            await session.execute(
                sa_text(
                    "SELECT before, after FROM audit_log WHERE action = 'promotion.ship.withdraw' "
                    "AND resource_id = :r"
                ),
                {"r": str(promo.id)},
            )
        ).one()
        assert audit[0] == {"ship_status": "待打单"}
        assert audit[1] == {"ship_status": "待发货", "reason": "地址写错了"}

    @pytest.mark.parametrize("ship_status", [None, "待发货", "已发货"])
    async def test_only_from_printing(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        ship_status: str | None,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status=ship_status
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_withdraw(
                promo.id, PromotionShipWithdrawRequest(reason="x"), flow_users.pr_manager
            )
        assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"

    async def test_pr_denied(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待打单"
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).ship_withdraw(
                promo.id, PromotionShipWithdrawRequest(reason="x"), flow_users.pr
            )
        assert exc_info.value.code == "PERMISSION_DENIED"
        assert (await _db_ship(session, promo.id))["ship_status"] == "待打单"


@pytest.mark.usefixtures("tenant_ctx")
class TestCancelLinkage:
    @pytest.mark.parametrize(
        ("before", "after"),
        [("待打单", "待发货"), ("待发货", "待发货"), ("已发货", "已发货"), (None, None)],
    )
    async def test_cancel_returns_printing_to_pending(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        before: str | None,
        after: str | None,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status=before
        )
        resp = await PromotionService(session).cancel(
            promo.id, PromotionCancelRequest(cancel_reason="博主不合作了"), flow_users.pr
        )
        assert (await _db_ship(session, promo.id))["ship_status"] == after
        assert resp.ship_status == after
        assert resp.publish_status == "已取消"

    async def test_cancelled_leaves_warehouse_and_pending_filter(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """待打单被取消 → 回到待发货，但不在「待打单」也不在推广列表「待发货」筛选里（阶段 = 已完结 · 不合作）。"""
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待打单"
        )
        svc = PromotionService(session)
        await svc.cancel(promo.id, PromotionCancelRequest(cancel_reason="取消"), flow_users.pr)
        for value in ("待打单", "待发货"):
            page = await svc.list_promotions(
                filters=PromotionListFilters(ship_status=value),
                page=1,
                page_size=100,
                user=flow_users.pr_manager,
            )
            assert promo.id not in {p.id for p in page.items}, value


@pytest.mark.usefixtures("tenant_ctx")
class TestCreateNeedShipping:
    async def test_route_default_null_and_checked_pending(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """主管直接新建：不勾「需要仓库发货」= NULL（补录历史，不进待推送）；勾了 = 待发货（11-58，C5）。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        body = {
            "style_id": str(style.id),
            "blogger_id": str(blogger.id),
            "platform": "小红书",
            "cooperation_mode": "送拍",
        }
        plain = await _call(session, flow_users.pr_manager, "POST", "/api/promotions/", body)
        assert plain.status_code == 201, plain.text
        assert plain.json()["ship_status"] is None
        checked = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            "/api/promotions/",
            {**body, "need_shipping": True},
        )
        assert checked.status_code == 201, checked.text
        assert checked.json()["ship_status"] == "待发货"
        got = {
            r[0]: r[1]
            for r in (
                await session.execute(
                    sa_text("SELECT id, ship_status FROM promotion WHERE id IN (:a, :b)"),
                    {"a": UUID(plain.json()["id"]), "b": UUID(checked.json()["id"])},
                )
            ).all()
        }
        assert got == {UUID(plain.json()["id"]): None, UUID(checked.json()["id"]): "待发货"}
        page = await PromotionService(session).list_promotions(
            filters=PromotionListFilters(ship_status="待发货"),
            page=1,
            page_size=100,
            user=flow_users.pr_manager,
        )
        assert {p.id for p in page.items} & set(got) == {UUID(checked.json()["id"])}


# ---------------------------------------------------------------------------
# S8 并发：30 个独立连接同时推送同一张单（都带补选明细）→ 1 成功、29 个 409、0 个 500（A3 / A8）
# ---------------------------------------------------------------------------


class TestShipPushConcurrent:
    async def test_30_concurrent_pushes(self, engine: Any) -> None:
        from app.core.security.auth import hash_password

        tenant_id = await default_tenant_id(engine)
        suffix = uuid4().hex[:8]
        promotion_id, style_id, sku_id, blogger_id = uuid4(), uuid4(), uuid4(), uuid4()
        user_ids = [uuid4(), uuid4()]  # 主管、管理员轮流推
        async with committed(engine) as seed:
            for uid, code in zip(user_ids, ("pr_manager", "admin"), strict=True):
                await seed.execute(
                    sa_text(
                        'INSERT INTO "user" (id, tenant_id, username, password_hash, status, '
                        "password_must_change, created_at, updated_at) "
                        "VALUES (:id, :tid, :un, :ph, 'active', false, NOW(), NOW())"
                    ),
                    {
                        "id": uid,
                        "tid": tenant_id,
                        "un": f"ship_{code}_{suffix}",
                        "ph": hash_password("Password123"),
                    },
                )
                await seed.execute(
                    sa_text(
                        "INSERT INTO user_role (id, tenant_id, user_id, role_id) "
                        "SELECT gen_random_uuid(), :tid, :uid, id FROM role WHERE code = :code"
                    ),
                    {"tid": tenant_id, "uid": uid, "code": code},
                )
            await seed.execute(
                sa_text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, category, "
                    "design_status, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :code, '并发款', '连衣裙', '大货', true, false, NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "code": f"SHIPC{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO sku (id, tenant_id, style_id, sku_code, color, size, base_price, "
                    "sourcing_type, is_active, is_deleted, created_at, updated_at) VALUES (:id, "
                    ":tid, :sid, :code, '黑色', 'M', 200, '自产', true, false, NOW(), NOW())"
                ),
                {"id": sku_id, "tid": tenant_id, "sid": style_id, "code": f"SHIPK{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :xhs, '并发博主', '小红书', false, true, false, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "xhs": f"XHS{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO promotion (id, tenant_id, style_id, blogger_id, pr_id, "
                    "internal_code, style_code_snapshot, style_short_name_snapshot, quote_amount, "
                    "platform, cooperation_date, publish_status, recall_status, settlement_status, "
                    "is_active, ship_status, receiver_name, receiver_phone, receiver_address, "
                    "created_at, updated_at) VALUES (:id, :tid, :sid, :bid, :uid, :code, 'SC', "
                    "'SN', 500.00, '小红书', :cd, '未发布', '未召回', '未核查', true, '待发货', "
                    "'张三', '13812345678', '杭州某路', NOW(), NOW())"
                ),
                {
                    "id": promotion_id,
                    "tid": tenant_id,
                    "sid": style_id,
                    "bid": blogger_id,
                    "uid": user_ids[0],
                    "code": f"SHIPPR{suffix}",
                    "cd": date(2026, 10, 9),
                },
            )

        n = 30
        barrier = asyncio.Barrier(n)

        async def attempt(s: AsyncSession, i: int) -> str:
            user = await s.get(User, user_ids[i % 2])  # 顺带先拿到连接，再一起出发
            assert user is not None
            await barrier.wait()
            try:
                await PromotionService(s).ship_push(
                    promotion_id,
                    PromotionShipPushRequest(items=[GoodsItemIn(style_id=style_id, sku_id=sku_id)]),
                    user,
                )
                return "ok"
            except StateTransitionConflictError:
                return "conflict"
            except Exception as e:
                return f"other:{type(e).__name__}:{getattr(e, 'code', '')}"

        try:
            results = await run_concurrently(engine, n, attempt, tenant_id=tenant_id)
            assert results.count("ok") == 1, results
            assert results.count("conflict") == n - 1, results
            winner = user_ids[results.index("ok") % 2]
            async with committed(engine) as check:
                row = (
                    await check.execute(
                        sa_text(
                            "SELECT ship_status, ship_pushed_by, "
                            "(SELECT COUNT(*) FROM promotion_item WHERE promotion_id = p.id) "
                            "FROM promotion p WHERE id = :id"
                        ),
                        {"id": promotion_id},
                    )
                ).one()
            assert tuple(row) == ("待打单", winner, 1)
        finally:
            async with committed(engine) as cleanup:
                await purge_promotions(cleanup, [promotion_id])
                await cleanup.execute(
                    sa_text("DELETE FROM audit_log WHERE resource_id = :r"),
                    {"r": str(promotion_id)},
                )
                for sql, key in (
                    ("DELETE FROM sku WHERE id = :id", sku_id),
                    ("DELETE FROM style WHERE id = :id", style_id),
                    ("DELETE FROM blogger WHERE id = :id", blogger_id),
                ):
                    await cleanup.execute(sa_text(sql), {"id": key})
                for uid in user_ids:
                    await cleanup.execute(
                        sa_text("DELETE FROM user_role WHERE user_id = :id"), {"id": uid}
                    )
                    await cleanup.execute(sa_text('DELETE FROM "user" WHERE id = :id'), {"id": uid})
