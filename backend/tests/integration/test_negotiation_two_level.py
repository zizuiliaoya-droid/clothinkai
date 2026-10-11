"""流程线 PR-3：谈款两级审核（设计 3.2、7.2、9.3 B 块）。

本文件按条目逐步长：S5 可见范围与读接口（列表 / 计数 / 详情的可见范围、超 36 小时、``ui``、收件投影、
``receiver_suggestion``、商品明细与品名、列表 SQL 条数不随行数涨）；S6 新建 / 编辑 / 提交（N1 ~ N3：
商品推定写回与明细、``star_create``、``others_in_progress``、转移全表的编辑 / 提交两列、提交缺项、提交留痕、goods-members）；
S7 两级审核（N4 ~ N7：审核人规则、通过不建推广单、转移全表的两级审核两列、退回重提后旧意见仍在事件里）；
S8 收货与定稿建单（N8、N9：转移全表的收货两列、复制到推广单、PR 不在时代录代确认、30 并发确认只建 1 张）；
S9 复制为新谈款与管理员转交（N10、N12：转移全表的复制 / 转交两列、复制哪些不复制哪些、两条线互相引用的事件、
转交后归属变化、新 PR 的资格、``admin_only``）；S10 博主卡片驳回记录与 ``others-in-progress``（跨人可见、
``negotiation_id`` 只给看得到的人、运营服务费 null、最近 5 条与总数、只有 3 个字段）。
角色一律用 ``flow_users``（迁移 seed 的真实角色）；造数用 ``negotiation_factory`` 直插任意状态。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from freezegun import freeze_time
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import event, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, IllegalStateTransitionError, PermissionDeniedError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, User, UserPermissionOverride
from app.modules.flow.exceptions import FlowActionForbiddenError, FlowGateMissingError
from app.modules.negotiation.enums import NegotiationFinalReviewAction, NegotiationReviewAction
from app.modules.negotiation.exceptions import (
    NegotiationNotFoundError,
    NegotiationSelfReviewForbiddenError,
    NegotiationStateConflictError,
)
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.repository import NegotiationRepository
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationListFilters,
    NegotiationReassignRequest,
    NegotiationReceiverConfirm,
    NegotiationReceiverUpdate,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import FINALIZED_CONFLICT_MESSAGE, NegotiationService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import InvalidReceiverPhoneError
from app.modules.promotion.schemas import GoodsItemIn, PromotionShipPushRequest, PromotionUpdate
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.concurrency import committed, default_tenant_id, run_concurrently
from tests.conftest import purge_negotiations

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_STATES = ("草稿", "主管待审", "审核驳回", "待老板审核", "老板退回", "待补收货地址", "已定稿")
_OVERDUE_KEY = "主管待审超时"
_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13812345678",
    "receiver_address": "浙江省杭州市西湖区某路 1 号",
}
_T0 = datetime(2026, 10, 10, 2, 0, tzinfo=UTC)
_APPROVE = NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE)
_FINAL_APPROVE = NegotiationFinalReviewRequest(action=NegotiationFinalReviewAction.APPROVE)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
def promotion_factory(promotion_factory: Any) -> Any:
    """一条用例建多张推广单时给足 8 位随机编号（照 ``test_promotion_shipping.py``）。"""
    create = promotion_factory.promotion

    async def promotion(**kw: Any) -> Any:
        kw.setdefault("internal_code", f"DENG{uuid4().hex[:8].upper()}")
        return await create(**kw)

    promotion_factory.promotion = promotion
    return promotion_factory


async def _goods(
    session: AsyncSession,
    tenant: Any,
    *styles: Any,
    is_suit: bool,
    short_name: str | None = None,
) -> GoodsMain:
    code = f"G{uuid4().hex[:8]}"
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=f"{code} 全称",
        short_name=short_name,
        is_suit=is_suit,
    )
    session.add(goods)
    await session.flush()
    for order, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=order
            )
        )
    await session.flush()
    return goods


async def _override(session: AsyncSession, tenant: Any, user: Any, scope: str) -> None:
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=perm.id, effect="grant"
        )
    )
    await session.flush()


class _World:
    """一个单品款式（带 SKU）+ 一个博主；``make`` 按状态造一张「提交必填项都齐」的谈款。"""

    def __init__(self, nf: Any, pf: Any, style: Any, sku: Any, blogger: Any) -> None:
        self._nf = nf
        self._pf = pf
        self.style = style
        self.sku = sku
        self.blogger = blogger

    async def make(self, pr: Any, status: str, **kw: Any) -> Any:
        kw.setdefault("items", [(self.style, self.sku)])
        kw.setdefault("scheduled_publish_date", date(2026, 11, 1))
        if status != "草稿":
            kw.setdefault("submitted_at", _T0)
        if status == "已定稿" and "promotion_id" not in kw:
            promo = await self._pf.promotion(style=self.style, blogger=self.blogger, pr=pr)
            kw["promotion_id"] = promo.id
        return await self._nf.negotiation(
            style=self.style, blogger=self.blogger, pr=pr, status=status, **kw
        )


@pytest.fixture
async def world(
    product_factory: Any,
    blogger_factory: Any,
    negotiation_factory: Any,
    promotion_factory: Any,
) -> _World:
    style = await product_factory.style(short_name="飞狐短裙", style_name="飞狐短裙全称")
    sku = await product_factory.sku(style, color="黑色", size="M")
    blogger = await blogger_factory.blogger()
    return _World(negotiation_factory, promotion_factory, style, sku, blogger)


async def _ids(svc: NegotiationService, user: Any, **filters: Any) -> set[Any]:
    page = await svc.list_negotiations(user=user, filters=NegotiationListFilters(**filters))
    return {n.id for n in page.items}


# ---------------------------------------------------------------------------
# 可见范围（3.2、7c-10；9.3 B「可见范围」）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestVisibility:
    async def test_pr_sees_only_own(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        mine_draft = await world.make(flow_users.pr, "草稿")
        mine_pending = await world.make(flow_users.pr, "主管待审")
        theirs = await world.make(flow_users.pr2, "主管待审")
        svc = NegotiationService(session)

        assert await _ids(svc, flow_users.pr) == {mine_draft.id, mine_pending.id}
        assert await _ids(svc, flow_users.pr2) == {theirs.id}
        # 传别人的 pr_id 返回空，不报错
        assert await _ids(svc, flow_users.pr2, pr_id=flow_users.pr.id) == set()

        counts = await svc.status_counts(flow_users.pr2)
        assert counts["主管待审"] == 1
        assert counts["草稿"] == 0

        with pytest.raises(NegotiationNotFoundError) as exc:
            await svc.get(mine_pending.id, flow_users.pr2)
        assert exc.value.code == "NEGOTIATION_NOT_FOUND"
        assert (await svc.get(mine_draft.id, flow_users.pr)).id == mine_draft.id

    async def test_nobody_else_sees_drafts(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        draft = await world.make(flow_users.pr, "草稿")
        pending = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        for viewer in (
            flow_users.pr_manager,
            flow_users.admin,
            flow_users.finance,
            flow_users.operations,
        ):
            assert await _ids(svc, viewer) == {pending.id}, viewer
            assert (await svc.status_counts(viewer))["草稿"] == 0
            with pytest.raises(NegotiationNotFoundError):
                await svc.get(draft.id, viewer)
            assert (await svc.get(pending.id, viewer)).id == pending.id

    async def test_proxy_needs_all_read_to_see_others(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        """主管请假的代理人：只授 ``negotiation.review:approve`` 看不到别人的单；再授 ``negotiation_all:read`` 才看得到。"""
        theirs = await world.make(flow_users.pr2, "主管待审")
        svc = NegotiationService(session)
        await _override(session, tenant_a, flow_users.pr, "negotiation.review:approve")
        assert await _ids(svc, flow_users.pr) == set()
        with pytest.raises(NegotiationNotFoundError):
            await svc.get(theirs.id, flow_users.pr)

        await _override(session, tenant_a, flow_users.pr, "negotiation_all:read")
        assert await _ids(svc, flow_users.pr) == {theirs.id}
        detail = await svc.get(theirs.id, flow_users.pr)
        assert detail.ui is not None
        assert detail.ui["actions"]["manager_review"] == {"state": "enabled"}

    async def test_counts_have_every_key(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        counts = await NegotiationService(session).status_counts(flow_users.pr)
        assert counts == {**dict.fromkeys(_STATES, 0), _OVERDUE_KEY: 0}

        await world.make(flow_users.pr, "老板退回")
        counts = await NegotiationService(session).status_counts(flow_users.pr)
        assert counts["老板退回"] == 1
        assert set(counts) == {*_STATES, _OVERDUE_KEY}

    async def test_status_filter_and_pending_first(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        draft = await world.make(flow_users.pr, "草稿")
        pending = await world.make(flow_users.pr, "主管待审")
        finalized = await world.make(flow_users.pr, "已定稿")
        svc = NegotiationService(session)

        page = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
        assert page.total == 3
        assert page.items[0].id == pending.id
        assert {n.id for n in page.items} == {draft.id, pending.id, finalized.id}
        assert await _ids(svc, flow_users.pr, status="已定稿") == {finalized.id}


# ---------------------------------------------------------------------------
# 字段投影：收件三项按字段规则、服务费照旧（4.6）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestProjection:
    async def test_receiver_hidden_from_finance_and_operations(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "待补收货地址", **_RECEIVER)
        svc = NegotiationService(session)

        for viewer in (flow_users.pr, flow_users.pr_manager, flow_users.admin):
            detail = await svc.get(n.id, viewer)
            assert {k: getattr(detail, k) for k in _RECEIVER} == _RECEIVER, viewer
        for viewer in (flow_users.finance, flow_users.operations):
            detail = await svc.get(n.id, viewer)
            assert {k: getattr(detail, k) for k in _RECEIVER} == dict.fromkeys(_RECEIVER), viewer
            row = next(
                r
                for r in (
                    await svc.list_negotiations(user=viewer, filters=NegotiationListFilters())
                ).items
                if r.id == n.id
            )
            assert {k: getattr(row, k) for k in _RECEIVER} == dict.fromkeys(_RECEIVER), viewer
        # 服务费：运营看不到，财务看得到（promotion.quote_amount 字段规则）
        assert (await svc.get(n.id, flow_users.operations)).quote_amount is None
        assert (await svc.get(n.id, flow_users.finance)).quote_amount == Decimal("500.00")

    async def test_new_columns_and_names(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        source = await world.make(flow_users.pr, "审核驳回")
        n = await world.make(
            flow_users.pr,
            "待补收货地址",
            copied_from_id=source.id,
            reviewed_by=flow_users.pr_manager.id,
            reviewed_at=_T0,
            review_opinion="可以",
            final_reviewed_by=flow_users.admin.id,
            final_reviewed_at=_T0,
            final_review_opinion="定稿",
        )
        detail = await NegotiationService(session).get(n.id, flow_users.pr)
        assert detail.line_id == n.line_id
        assert detail.copied_from_id == source.id
        assert detail.final_reviewed_by == flow_users.admin.id
        assert detail.final_reviewer_name == (
            flow_users.admin.display_name or flow_users.admin.username
        )
        assert detail.final_reviewed_at == _T0
        assert detail.final_review_opinion == "定稿"
        assert detail.receiver_confirmed_at is None
        assert detail.receiver_confirmed_by is None


# ---------------------------------------------------------------------------
# 商品明细与品名（7.2 响应 items、display_short_name）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestItemsAndNames:
    async def test_display_short_name_fallbacks(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        world: _World,
        negotiation_factory: Any,
    ) -> None:
        """商品简称 → 款式简称 → 款式名。"""
        with_short = await product_factory.style(short_name="款简称", style_name="款全称 A")
        no_short = await product_factory.style(short_name="  ", style_name="款全称 B")
        goods = await _goods(session, tenant_a, with_short, is_suit=False, short_name="商品简称")
        blank_goods = await _goods(session, tenant_a, with_short, is_suit=False, short_name="  ")
        cases = {
            "商品简称": (with_short, goods.id),
            "款简称": (with_short, blank_goods.id),
            "款全称 B": (no_short, None),
        }
        made = {}
        for expected, (style, goods_main_id) in cases.items():
            n = await negotiation_factory.negotiation(
                style=style, blogger=world.blogger, pr=flow_users.pr, goods_main_id=goods_main_id
            )
            made[n.id] = expected
        svc = NegotiationService(session)
        page = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
        assert {r.id: r.display_short_name for r in page.items} == made
        for nid, expected in made.items():
            assert (await svc.get(nid, flow_users.pr)).display_short_name == expected

    async def test_items_and_submit_missing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        world: _World,
        negotiation_factory: Any,
    ) -> None:
        top = await product_factory.style(short_name="上衣", style_name="条纹上衣全称")
        pants = await product_factory.style(short_name=None, style_name="阔腿裤")
        top_sku = await product_factory.sku(top, color="黑色", size="M")
        pants_sku = await product_factory.sku(pants, color="白色", size="L")
        other_sku = await product_factory.sku(world.style, color="红", size="S")
        suit = await _goods(session, tenant_a, top, pants, is_suit=True)
        common = {
            "style": top,
            "blogger": world.blogger,
            "pr": flow_users.pr,
            "goods_main_id": suit.id,
            "scheduled_publish_date": date(2026, 11, 1),
        }
        half = await negotiation_factory.negotiation(
            items=[(top, top_sku), (pants, None)], **common
        )
        full = await negotiation_factory.negotiation(
            items=[(top, top_sku), (pants, pants_sku)], **common
        )
        # 有一行的 sku 不属于该行款式：同样算没选齐
        wrong = await negotiation_factory.negotiation(
            items=[(top, top_sku), (pants, other_sku)], **common
        )
        svc = NegotiationService(session)

        detail = await svc.get(half.id, flow_users.pr)
        assert [
            (i.style_id, i.display_short_name, i.goods_title, i.sku_id, i.color, i.size)
            for i in detail.items
        ] == [
            (top.id, "上衣", "条纹上衣全称", top_sku.id, "黑色", "M"),
            (pants.id, "阔腿裤", "阔腿裤", None, None, None),
        ]
        assert detail.items[0].style_code == top.style_code
        assert detail.items[0].style_main_image_url is None

        page = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
        rows = {r.id: r for r in page.items}

        def _missing(ui: Any) -> list[str]:
            return [m["key"] for m in ui["actions"]["submit"].get("missing", [])]

        assert _missing(rows[half.id].ui) == ["goods_items"]
        assert _missing(rows[wrong.id].ui) == ["goods_items"]
        assert _missing(rows[full.id].ui) == []
        assert len(rows[full.id].items) == 2
        # 列表行 = actions + edits，详情 = actions + fields（7.1）
        assert set(rows[full.id].ui) == {"column", "actions", "edits"}
        assert set(detail.ui or {}) == {"column", "actions", "fields"}
        assert _missing(detail.ui) == ["goods_items"]


# ---------------------------------------------------------------------------
# ui：行级与页级（7.1、8.3）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestUi:
    async def test_page_level_create(self, session: AsyncSession, flow_users: Any) -> None:
        svc = NegotiationService(session)

        async def _create(user: Any) -> Any:
            page = await svc.list_negotiations(user=user, filters=NegotiationListFilters())
            return page.ui["actions"].get("create")

        assert await _create(flow_users.pr) == {"state": "enabled"}
        assert await _create(flow_users.pr_manager) == {"state": "enabled"}
        assert await _create(flow_users.admin) == {
            "state": "disabled",
            "reason": "管理员建的谈款没人能定稿",
        }
        assert await _create(flow_users.finance) is None
        assert await _create(flow_users.operations) is None

    async def test_row_ui_per_role(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        pending = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        manager = await svc.get(pending.id, flow_users.pr_manager)
        assert manager.ui is not None
        assert manager.ui["column"] == "主管待审"
        assert manager.ui["actions"] == {"manager_review": {"state": "enabled"}}
        admin = await svc.get(pending.id, flow_users.admin)
        assert admin.ui is not None
        assert admin.ui["actions"]["manager_review"] == {
            "state": "disabled",
            "reason": "第一级由 PR 主管审核，管理员不能代做",
        }
        finance = await svc.get(pending.id, flow_users.finance)
        assert finance.ui is not None
        assert finance.ui["actions"] == {}
        assert "receiver" not in finance.ui["fields"]


# ---------------------------------------------------------------------------
# 超 36 小时（7c-6；9.3 B「超 36 小时」）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestOverdue:
    async def _row(self, svc: NegotiationService, user: Any, nid: Any, **filters: Any) -> Any:
        page = await svc.list_negotiations(user=user, filters=NegotiationListFilters(**filters))
        return next((r for r in page.items if r.id == nid), None)

    async def test_exactly_36_hours_is_not_overdue(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        with freeze_time(_T0 + timedelta(hours=36)):
            row = await self._row(svc, flow_users.pr_manager, n.id)
            assert (row.overdue, row.overdue_hours) == (False, 36)
            assert (await svc.status_counts(flow_users.pr_manager))[_OVERDUE_KEY] == 0
            assert await self._row(svc, flow_users.pr_manager, n.id, overdue_only=True) is None

    async def test_36_hours_1_minute_is_overdue(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "主管待审")
        other = await world.make(flow_users.pr, "待老板审核")  # 不是主管待审：不算超时
        svc = NegotiationService(session)
        with freeze_time(_T0 + timedelta(hours=36, minutes=1)):
            row = await self._row(svc, flow_users.pr_manager, n.id)
            assert (row.overdue, row.overdue_hours) == (True, 36)
            detail = await svc.get(n.id, flow_users.pr_manager)
            assert (detail.overdue, detail.overdue_hours) == (True, 36)
            assert (await svc.status_counts(flow_users.pr_manager))[_OVERDUE_KEY] == 1
            assert await _ids(svc, flow_users.pr_manager, overdue_only=True) == {n.id}
            other_row = await self._row(svc, flow_users.pr_manager, other.id)
            assert (other_row.overdue, other_row.overdue_hours) == (False, None)
            # 运营、财务没有高亮（矩阵 overdue_badge：读/读/读/隐/隐）
            ops_row = await self._row(svc, flow_users.operations, n.id)
            assert (ops_row.overdue, ops_row.overdue_hours) == (False, None)

    async def test_resubmit_restarts_the_clock(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "草稿", submitted_at=_T0 - timedelta(hours=100))
        svc = NegotiationService(session)
        with freeze_time(_T0):
            await svc.submit(n.id, flow_users.pr)
        with freeze_time(_T0 + timedelta(hours=1)):
            row = await self._row(svc, flow_users.pr_manager, n.id)
            assert (row.overdue, row.overdue_hours) == (False, 1)
            assert (await svc.status_counts(flow_users.pr_manager))[_OVERDUE_KEY] == 0
        with freeze_time(_T0 + timedelta(hours=37)):
            row = await self._row(svc, flow_users.pr_manager, n.id)
            assert (row.overdue, row.overdue_hours) == (True, 37)


# ---------------------------------------------------------------------------
# 收货信息默认带出（3.2；9.3 B「默认带出地址」）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverSuggestion:
    async def _set_created(self, session: AsyncSession, promotion_id: Any, at: datetime) -> None:
        await session.execute(
            sa_text("UPDATE promotion SET created_at = CAST(:at AS timestamptz) WHERE id = :pid"),
            {"at": at, "pid": promotion_id},
        )

    async def test_latest_promotion_with_receiver(
        self,
        session: AsyncSession,
        flow_users: Any,
        world: _World,
        promotion_factory: Any,
    ) -> None:
        older = await promotion_factory.promotion(
            style=world.style,
            blogger=world.blogger,
            pr=flow_users.pr,
            receiver_name="李四",
            receiver_phone="13900000000",
            receiver_address="旧地址",
        )
        newer = await promotion_factory.promotion(
            style=world.style,
            blogger=world.blogger,
            pr=flow_users.pr,
            **_RECEIVER,
        )
        latest_empty = await promotion_factory.promotion(
            style=world.style, blogger=world.blogger, pr=flow_users.pr
        )
        await self._set_created(session, older.id, datetime(2026, 9, 1, 2, tzinfo=UTC))
        await self._set_created(session, newer.id, datetime(2026, 10, 3, 2, tzinfo=UTC))
        await self._set_created(session, latest_empty.id, datetime(2026, 10, 5, 2, tzinfo=UTC))
        n = await world.make(flow_users.pr, "待补收货地址")
        svc = NegotiationService(session)

        detail = await svc.get(n.id, flow_users.pr)
        assert detail.receiver_suggestion is not None
        assert detail.receiver_suggestion.model_dump() == {
            **_RECEIVER,
            "source": "promotion",
            "source_label": f"推广单 {newer.internal_code} · 10-03",
        }
        # 主管、管理员能代录，也带；财务、运营不能录，不带；列表不带
        for viewer in (flow_users.pr_manager, flow_users.admin):
            assert (await svc.get(n.id, viewer)).receiver_suggestion is not None
        for viewer in (flow_users.finance, flow_users.operations):
            assert (await svc.get(n.id, viewer)).receiver_suggestion is None
        page = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
        assert all(r.receiver_suggestion is None for r in page.items)

    async def test_falls_back_to_finalized_negotiation(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        await world.make(
            flow_users.pr2,
            "已定稿",
            receiver_confirmed_at=datetime(2026, 10, 2, 2, tzinfo=UTC),
            **_RECEIVER,
        )
        n = await world.make(flow_users.pr, "待补收货地址")
        detail = await NegotiationService(session).get(n.id, flow_users.pr)
        assert detail.receiver_suggestion is not None
        assert detail.receiver_suggestion.model_dump() == {
            **_RECEIVER,
            "source": "negotiation",
            "source_label": "谈款定稿 · 10-02",
        }

    async def test_none_and_only_when_receiver_pending(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "待补收货地址")
        svc = NegotiationService(session)
        assert (await svc.get(n.id, flow_users.pr)).receiver_suggestion is None

        await world.make(flow_users.pr, "已定稿", **_RECEIVER)
        other = await world.make(flow_users.pr, "待老板审核")
        assert (await svc.get(n.id, flow_users.pr)).receiver_suggestion is not None
        assert (await svc.get(other.id, flow_users.pr)).receiver_suggestion is None


# ---------------------------------------------------------------------------
# 列表 SQL 条数不随行数涨（7.1：ui 只用本页数据算）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
async def test_list_queries_do_not_grow_with_rows(
    session: AsyncSession,
    engine: Any,
    tenant_a: Any,
    flow_users: Any,
    world: _World,
) -> None:
    goods = await _goods(session, tenant_a, world.style, is_suit=False)
    svc = NegotiationService(session)

    async def _count() -> tuple[int, Any]:
        statements: list[str] = []

        def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _capture)
        try:
            page = await svc.list_negotiations(
                user=flow_users.pr, filters=NegotiationListFilters(), page_size=100
            )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _capture)
        return len(statements), page

    await world.make(flow_users.pr, "主管待审", goods_main_id=goods.id)
    one_row, _ = await _count()
    for status in ("草稿", "老板退回", "待补收货地址", "已定稿"):
        await world.make(flow_users.pr, status, goods_main_id=goods.id)
    five_rows, page = await _count()
    assert page.total == 5
    assert five_rows == one_row
    assert all(len(r.items) == 1 for r in page.items)


# ---------------------------------------------------------------------------
# S6 新建（N1；7.2 POST /）
# ---------------------------------------------------------------------------


async def _events(session: AsyncSession, negotiation_id: Any) -> list[Any]:
    return list(
        (
            await session.execute(
                sa_text(
                    "SELECT event_type, from_state, to_state, payload, actor_id, line_id, note"
                    " FROM line_event WHERE negotiation_id = :n ORDER BY seq"
                ),
                {"n": negotiation_id},
            )
        ).all()
    )


def _create_body(world: _World, **kw: Any) -> NegotiationCreate:
    kw.setdefault("cooperation_mode", CooperationMode.GIFT)
    kw.setdefault("scheduled_publish_date", date(2026, 11, 1))
    return NegotiationCreate(blogger_id=world.blogger.id, style_id=world.style.id, **kw)


class _Suit:
    """上衣 + 阔腿裤的套装（各带 1 个 SKU），上衣另有一个单卖的商品（N1 推定非套装优先）。"""

    def __init__(self, top: Any, pants: Any, top_sku: Any, pants_sku: Any, suit: Any) -> None:
        self.top = top
        self.pants = pants
        self.top_sku = top_sku
        self.pants_sku = pants_sku
        self.suit = suit


@pytest.fixture
async def suit(session: AsyncSession, tenant_a: Any, product_factory: Any) -> _Suit:
    top = await product_factory.style(short_name="上衣", style_name="条纹上衣全称")
    pants = await product_factory.style(short_name=None, style_name="阔腿裤")
    top_sku = await product_factory.sku(top, color="黑色", size="M")
    pants_sku = await product_factory.sku(pants, color="白色", size="L")
    goods = await _goods(session, tenant_a, top, pants, is_suit=True)
    return _Suit(top, pants, top_sku, pants_sku, goods)


@pytest.mark.usefixtures("tenant_ctx")
class TestCreate:
    async def test_single_style_without_goods(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """款式不属于任何商品：按单品建 1 行空明细、goods_main_id 留空；发 line_id、写 negotiation.created。"""
        resp = await NegotiationService(session).create(_create_body(world), flow_users.pr)
        assert (resp.status, resp.goods_main_id) == ("草稿", None)
        assert [(i.style_id, i.sku_id) for i in resp.items] == [(world.style.id, None)]
        assert resp.others_in_progress == []
        events = await _events(session, resp.id)
        assert [(e[0], e[1], e[2], e[4], e[5]) for e in events] == [
            ("negotiation.created", None, "草稿", flow_users.pr.id, resp.line_id)
        ]
        stored = await session.get(Negotiation, resp.id)
        assert stored is not None and stored.line_id == resp.line_id

    async def test_goods_inferred_and_written_back(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        blogger_factory: Any,
        suit: _Suit,
    ) -> None:
        """不传商品：款式同时单卖与进套装 → 推定非套装那个并写回；显式传套装 → 按套装成员建 2 行。"""
        single = await _goods(session, tenant_a, suit.top, is_suit=False)
        blogger = await blogger_factory.blogger()
        svc = NegotiationService(session)
        inferred = await svc.create(
            NegotiationCreate(
                blogger_id=blogger.id,
                style_id=suit.top.id,
                cooperation_mode=CooperationMode.GIFT,
            ),
            flow_users.pr,
        )
        assert inferred.goods_main_id == single.id
        assert [(i.style_id, i.sku_id) for i in inferred.items] == [(suit.top.id, None)]
        stored = await session.get(Negotiation, inferred.id)
        assert stored is not None and stored.goods_main_id == single.id

        explicit = await svc.create(
            NegotiationCreate(
                blogger_id=blogger.id,
                style_id=suit.top.id,
                goods_main_id=suit.suit.id,
                cooperation_mode=CooperationMode.GIFT,
            ),
            flow_users.pr,
        )
        assert explicit.goods_main_id == suit.suit.id
        assert [i.style_id for i in explicit.items] == [suit.top.id, suit.pants.id]
        assert all(i.sku_id is None for i in explicit.items)

    async def test_explicit_items_validated(
        self, session: AsyncSession, flow_users: Any, world: _World, suit: _Suit
    ) -> None:
        svc = NegotiationService(session)
        base = {
            "blogger_id": world.blogger.id,
            "style_id": suit.top.id,
            "goods_main_id": suit.suit.id,
            "cooperation_mode": CooperationMode.GIFT,
        }
        ok = await svc.create(
            NegotiationCreate(
                **base,
                items=[
                    GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id),
                    GoodsItemIn(style_id=suit.pants.id),
                ],
            ),
            flow_users.pr,
        )
        assert [(i.style_id, i.sku_id) for i in ok.items] == [
            (suit.top.id, suit.top_sku.id),
            (suit.pants.id, None),
        ]

        async def _rejected(items: list[GoodsItemIn]) -> AppException:
            with pytest.raises(AppException) as exc:
                await svc.create(NegotiationCreate(**base, items=items), flow_users.pr)
            return exc.value

        short = await _rejected([GoodsItemIn(style_id=suit.top.id)])
        assert (short.code, short.details["missing_style_ids"]) == (
            "VALIDATION_ERROR",
            [str(suit.pants.id)],
        )
        extra = await _rejected(
            [
                GoodsItemIn(style_id=suit.top.id),
                GoodsItemIn(style_id=suit.pants.id),
                GoodsItemIn(style_id=world.style.id),
            ]
        )
        assert (extra.code, extra.details["extra_style_ids"]) == (
            "VALIDATION_ERROR",
            [str(world.style.id)],
        )
        twice = await _rejected(
            [GoodsItemIn(style_id=suit.top.id), GoodsItemIn(style_id=suit.top.id)]
        )
        assert (twice.code, twice.details["duplicate_style_ids"]) == (
            "VALIDATION_ERROR",
            [str(suit.top.id)],
        )
        wrong_sku = await _rejected(
            [
                GoodsItemIn(style_id=suit.top.id, sku_id=suit.pants_sku.id),
                GoodsItemIn(style_id=suit.pants.id),
            ]
        )
        assert wrong_sku.code == "INVALID_SKU_REFERENCE"

    async def test_admin_cannot_create(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """持 ``*`` 建谈款 → 403 ``star_create``（11-1：管理员建的谈款没人能定稿），库里不留单。"""
        with pytest.raises(FlowActionForbiddenError) as exc:
            await NegotiationService(session).create(_create_body(world), flow_users.admin)
        assert exc.value.code == "FLOW_ACTION_FORBIDDEN"
        assert exc.value.details == {"rule": "star_create", "reason": "管理员建的谈款没人能定稿"}
        count = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM negotiation WHERE pr_id = :u"),
                {"u": flow_users.admin.id},
            )
        ).scalar_one()
        assert count == 0
        # 主管持 negotiation:write、不持 *：能建（主管自建，11-24）
        made = await NegotiationService(session).create(_create_body(world), flow_users.pr_manager)
        assert made.pr_id == flow_users.pr_manager.id

    async def test_others_in_progress_hint(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """同博主别的 PR 在谈（7c-16）：只提示不拦；只给姓名、状态、更新时间；不含自己的、不含终态。"""
        theirs = await world.make(flow_users.pr2, "老板退回")
        await world.make(flow_users.pr2, "已定稿")
        await world.make(flow_users.pr2, "审核驳回")
        await world.make(flow_users.pr, "主管待审")
        resp = await NegotiationService(session).create(_create_body(world), flow_users.pr)
        assert resp.status == "草稿"
        assert resp.others_in_progress is not None
        assert [o.model_dump() for o in resp.others_in_progress] == [
            {
                "pr_name": flow_users.pr2.display_name or flow_users.pr2.username,
                "status": "老板退回",
                "updated_at": theirs.updated_at,
            }
        ]
        # 读接口不带这个提示
        assert (
            await NegotiationService(session).get(resp.id, flow_users.pr)
        ).others_in_progress is None


# ---------------------------------------------------------------------------
# S6 编辑（N2；7.2 PUT /{id}）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestEdit:
    async def test_returned_stays_returned(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """老板退回改完状态不变（不再像旧逻辑那样回草稿），退回意见还在。"""
        n = await world.make(
            flow_users.pr,
            "老板退回",
            final_reviewed_by=flow_users.admin.id,
            final_reviewed_at=_T0,
            final_review_opinion="价格再压一压",
        )
        resp = await NegotiationService(session).update(
            n.id,
            NegotiationUpdate(remark="改了报价", quote_amount=Decimal("300.00")),
            flow_users.pr,
        )
        assert (resp.status, resp.remark, resp.quote_amount) == (
            "老板退回",
            "改了报价",
            Decimal("300.00"),
        )
        assert resp.final_review_opinion == "价格再压一压"
        # 编辑不单独写事件（改了什么在提交时一次算，N2）
        assert await _events(session, n.id) == []

    async def test_only_owner_edits(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        returned = await world.make(flow_users.pr, "老板退回")
        draft = await world.make(flow_users.pr, "草稿")
        svc = NegotiationService(session)
        for other in (flow_users.pr_manager, flow_users.admin):
            with pytest.raises(FlowActionForbiddenError) as exc:
                await svc.update(returned.id, NegotiationUpdate(remark="代改"), other)
            assert exc.value.details["rule"] == "not_owner", other
            # 草稿只有本人看得到：别人改 → 404
            with pytest.raises(NegotiationNotFoundError):
                await svc.update(draft.id, NegotiationUpdate(remark="代改"), other)
        with pytest.raises(NegotiationNotFoundError):
            await svc.update(returned.id, NegotiationUpdate(remark="代改"), flow_users.pr2)

    async def test_change_goods_rebuilds_items(
        self, session: AsyncSession, flow_users: Any, world: _World, suit: _Suit
    ) -> None:
        """换款式 / 商品：同 N1 推定并写回，明细整组重建、新行 sku 为空；只改备注明细不动。"""
        n = await world.make(flow_users.pr, "草稿")
        svc = NegotiationService(session)
        same = await svc.update(n.id, NegotiationUpdate(remark="只改备注"), flow_users.pr)
        assert [(i.style_id, i.sku_id) for i in same.items] == [(world.style.id, world.sku.id)]

        moved = await svc.update(n.id, NegotiationUpdate(style_id=suit.top.id), flow_users.pr)
        # 上衣只在套装里：推定到套装，按 2 个成员重建空行
        assert moved.goods_main_id == suit.suit.id
        assert [(i.style_id, i.sku_id) for i in moved.items] == [
            (suit.top.id, None),
            (suit.pants.id, None),
        ]

        picked = await svc.update(
            n.id,
            NegotiationUpdate(
                items=[
                    GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id),
                    GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id),
                ]
            ),
            flow_users.pr,
        )
        assert [i.sku_id for i in picked.items] == [suit.top_sku.id, suit.pants_sku.id]
        assert picked.ui is not None
        assert picked.ui["actions"]["submit"] == {"state": "enabled"}

    async def test_conflict_when_state_moved(
        self, session: AsyncSession, flow_users: Any, world: _World, monkeypatch: Any
    ) -> None:
        """条件 UPDATE：状态已经不在草稿 / 老板退回（或不是本人）→ 0 行；service 转 409。"""
        n = await world.make(flow_users.pr, "主管待审")
        repo = NegotiationRepository(session)
        args = {
            "negotiation_id": n.id,
            "tenant_id": n.tenant_id,
            "from_statuses": ("草稿", "老板退回"),
            "values": {"remark": "x"},
        }
        assert await repo.update_state(**args) is None
        draft = await world.make(flow_users.pr, "草稿")
        args["negotiation_id"] = draft.id
        assert await repo.update_state(**args, pr_id=flow_users.pr2.id) is None
        assert (await repo.update_state(**args, pr_id=flow_users.pr.id)) is not None

        pending = await world.make(flow_users.pr, "主管待审")
        final_pending = await world.make(
            flow_users.pr, "待老板审核", reviewed_by=flow_users.pr_manager.id
        )

        async def _lost(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(NegotiationRepository, "update_state", _lost)
        svc = NegotiationService(session)
        for call in (
            svc.update(draft.id, NegotiationUpdate(remark="y"), flow_users.pr),
            svc.submit(draft.id, flow_users.pr),
            svc.review(pending.id, _APPROVE, flow_users.pr_manager),
            svc.final_review(final_pending.id, _FINAL_APPROVE, flow_users.admin),
        ):
            with pytest.raises(IllegalStateTransitionError) as exc:
                await call
            assert (exc.value.code, exc.value.status_code) == ("NEGOTIATION_STATE_CONFLICT", 409)


# ---------------------------------------------------------------------------
# S6 转移全表：编辑 / 提交两列（9.3 B「转移全表」）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestTransitions:
    @pytest.mark.parametrize("status", _STATES)
    async def test_edit(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status in ("草稿", "老板退回"):
            resp = await svc.update(n.id, NegotiationUpdate(remark="改"), flow_users.pr)
            assert (resp.status, resp.remark) == (status, "改")
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.update(n.id, NegotiationUpdate(remark="改"), flow_users.pr)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "edit"}

    @pytest.mark.parametrize("status", _STATES)
    async def test_submit(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status in ("草稿", "老板退回"):
            resp = await svc.submit(n.id, flow_users.pr)
            assert resp.status == "主管待审"
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.submit(n.id, flow_users.pr)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "submit"}

    @pytest.mark.parametrize("status", _STATES)
    async def test_manager_review(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status == "草稿":
            # 草稿只有本人看得到：主管取不到 → 404（先于状态机）
            with pytest.raises(NegotiationNotFoundError):
                await svc.review(n.id, _APPROVE, flow_users.pr_manager)
            return
        if status == "主管待审":
            resp = await svc.review(n.id, _APPROVE, flow_users.pr_manager)
            assert resp.status == "待老板审核"
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.review(n.id, _APPROVE, flow_users.pr_manager)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "manager_review"}

    @pytest.mark.parametrize("status", _STATES)
    async def test_final_review(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status, reviewed_by=flow_users.pr_manager.id)
        svc = NegotiationService(session)
        if status == "草稿":
            with pytest.raises(NegotiationNotFoundError):
                await svc.final_review(n.id, _FINAL_APPROVE, flow_users.admin)
            return
        if status == "待老板审核":
            resp = await svc.final_review(n.id, _FINAL_APPROVE, flow_users.admin)
            assert resp.status == "待补收货地址"
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.final_review(n.id, _FINAL_APPROVE, flow_users.admin)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "final_review"}


# ---------------------------------------------------------------------------
# S6 提交（N3）：缺项一次全报、留痕
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestSubmit:
    async def test_legacy_draft_without_items(
        self, session: AsyncSession, flow_users: Any, world: _World, negotiation_factory: Any
    ) -> None:
        """上线前的存量草稿（直插、没有明细、没填约定发布时间）直接提交 → 422，缺项一次全报、与 ui 一致。"""
        n = await negotiation_factory.negotiation(
            style=world.style, blogger=world.blogger, pr=flow_users.pr
        )
        svc = NegotiationService(session)
        ui_missing = [
            m["key"]
            for m in ((await svc.get(n.id, flow_users.pr)).ui or {})["actions"]["submit"]["missing"]
        ]
        with pytest.raises(FlowGateMissingError) as exc:
            await svc.submit(n.id, flow_users.pr)
        assert exc.value.code == "FLOW_GATE_MISSING"
        keys = [m["key"] for m in exc.value.details["missing"]]
        assert keys == ["goods_items", "scheduled_publish_date"]
        assert keys == ui_missing
        assert (await svc.get(n.id, flow_users.pr)).status == "草稿"

    async def test_suit_missing_member(
        self,
        session: AsyncSession,
        flow_users: Any,
        world: _World,
        suit: _Suit,
        negotiation_factory: Any,
    ) -> None:
        """套装少一个成员的明细（每行都有 sku）提交 → 422 缺颜色尺码（只查「每行有 sku」会放过它）。"""
        n = await negotiation_factory.negotiation(
            style=suit.top,
            blogger=world.blogger,
            pr=flow_users.pr,
            goods_main_id=suit.suit.id,
            scheduled_publish_date=date(2026, 11, 1),
            items=[(suit.top, suit.top_sku)],
        )
        with pytest.raises(FlowGateMissingError) as exc:
            await NegotiationService(session).submit(n.id, flow_users.pr)
        assert [m["key"] for m in exc.value.details["missing"]] == ["goods_items"]

    async def test_only_owner_submits(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await world.make(flow_users.pr, "老板退回")
        with pytest.raises(FlowActionForbiddenError) as exc:
            await NegotiationService(session).submit(n.id, flow_users.pr_manager)
        assert exc.value.details["rule"] == "not_owner"

    async def test_resubmit_keeps_trail(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """提交写 submitted_at、清本轮两级审核；事件带快照，重提再带与上次提交的 diff（9.3 B「留痕」）。"""
        svc = NegotiationService(session)
        created = await svc.create(
            _create_body(world, items=[GoodsItemIn(style_id=world.style.id, sku_id=world.sku.id)]),
            flow_users.pr,
        )
        with freeze_time(_T0):
            first = await svc.submit(created.id, flow_users.pr)
        assert (first.status, first.submitted_at) == ("主管待审", _T0)

        # 主管通过、老板退回
        await svc.review(
            created.id,
            NegotiationReviewRequest(
                action=NegotiationReviewAction.APPROVE, review_opinion="主管：可以"
            ),
            flow_users.pr_manager,
        )
        returned = await svc.final_review(
            created.id,
            NegotiationFinalReviewRequest(
                action=NegotiationFinalReviewAction.RETURN, opinion="老板：价格再压"
            ),
            flow_users.admin,
        )
        assert (returned.status, returned.review_opinion, returned.final_review_opinion) == (
            "老板退回",
            "主管：可以",
            "老板：价格再压",
        )

        await svc.update(
            created.id, NegotiationUpdate(quote_amount=Decimal("300.00")), flow_users.pr
        )
        later = _T0 + timedelta(days=1)
        with freeze_time(later):
            second = await svc.submit(created.id, flow_users.pr)
        assert (second.status, second.submitted_at) == ("主管待审", later)
        assert (second.reviewed_by, second.reviewed_at, second.review_opinion) == (None, None, None)
        assert (
            second.final_reviewed_by,
            second.final_reviewed_at,
            second.final_review_opinion,
        ) == (None, None, None)

        events = await _events(session, created.id)
        # 重提清了行上的两级意见，上一轮的意见仍在事件里（9.3 B「留痕」，B9）
        assert [(e[0], e[1], e[2], e[6]) for e in events] == [
            ("negotiation.created", None, "草稿", None),
            ("negotiation.submitted", "草稿", "主管待审", None),
            ("negotiation.manager_approved", "主管待审", "待老板审核", "主管：可以"),
            ("negotiation.final_returned", "待老板审核", "老板退回", "老板：价格再压"),
            ("negotiation.submitted", "老板退回", "主管待审", None),
        ]
        snapshot = {
            "blogger_id": str(world.blogger.id),
            "style_id": str(world.style.id),
            "goods_main_id": None,
            "goods_items": [[str(world.style.id), str(world.sku.id)]],
            "cooperation_mode": "送拍",
            "platform": "小红书",
            "scheduled_publish_date": "2026-11-01",
            "quote_amount": "500.00",
        }
        assert events[1][3] == {"snapshot": snapshot}
        assert events[4][3] == {
            "snapshot": {**snapshot, "quote_amount": "300.00"},
            "diff": {"quote_amount": {"before": "500.00", "after": "300.00"}},
        }
        assert [e[4] for e in events] == [
            flow_users.pr.id,
            flow_users.pr.id,
            flow_users.pr_manager.id,
            flow_users.admin.id,
            flow_users.pr.id,
        ]

    async def test_submit_response_has_others_in_progress(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        await world.make(flow_users.pr2, "主管待审")
        n = await world.make(flow_users.pr, "草稿")
        resp = await NegotiationService(session).submit(n.id, flow_users.pr)
        assert resp.others_in_progress is not None
        assert [o.status for o in resp.others_in_progress] == ["主管待审"]


# ---------------------------------------------------------------------------
# S6 goods-members（偏差 3）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestGoodsMembers:
    async def test_members(
        self, session: AsyncSession, flow_users: Any, world: _World, suit: _Suit
    ) -> None:
        svc = NegotiationService(session)
        single = await svc.goods_members(style_id=world.style.id, goods_main_id=None)
        assert single.goods_main_id is None
        assert [m.model_dump() for m in single.members] == [
            {
                "style_id": world.style.id,
                "display_short_name": "飞狐短裙",
                "goods_title": "飞狐短裙全称",
            }
        ]
        inferred = await svc.goods_members(style_id=suit.pants.id, goods_main_id=None)
        assert inferred.goods_main_id == suit.suit.id
        assert [(m.style_id, m.display_short_name) for m in inferred.members] == [
            (suit.top.id, "上衣"),
            (suit.pants.id, "阔腿裤"),
        ]

        with pytest.raises(AppException) as exc:
            await svc.goods_members(style_id=world.style.id, goods_main_id=suit.suit.id)
        assert exc.value.code == "INVALID_NEGOTIATION_GOODS"
        with pytest.raises(AppException) as exc:
            await svc.goods_members(style_id=uuid4(), goods_main_id=None)
        assert exc.value.code == "INVALID_NEGOTIATION_STYLE"


# ---------------------------------------------------------------------------
# S7 两级审核（N4 ~ N7；7.2 POST /{id}/review、POST /{id}/final-review）
# ---------------------------------------------------------------------------


async def _promotion_count(session: AsyncSession, line_id: Any) -> int:
    return int(
        (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM promotion WHERE line_id = :l"), {"l": line_id}
            )
        ).scalar_one()
    )


@pytest.mark.usefixtures("tenant_ctx")
class TestTwoLevelReview:
    async def test_approve_then_finalize(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """主管通过 → 待老板审核；老板定稿 → 待补收货地址。两级各记审核人 / 时间 / 意见、各写一条事件（意见进 note），推广单 0。"""
        n = await world.make(flow_users.pr, "主管待审")
        line_id = n.line_id
        svc = NegotiationService(session)
        with freeze_time(_T0):
            first = await svc.review(
                n.id,
                NegotiationReviewRequest(
                    action=NegotiationReviewAction.APPROVE, review_opinion="价格合适"
                ),
                flow_users.pr_manager,
            )
        assert (first.status, first.reviewed_by, first.reviewed_at, first.review_opinion) == (
            "待老板审核",
            flow_users.pr_manager.id,
            _T0,
            "价格合适",
        )
        assert first.reviewer_name is not None
        assert first.promotion_id is None
        assert await _promotion_count(session, line_id) == 0

        later = _T0 + timedelta(hours=2)
        with freeze_time(later):
            second = await svc.final_review(n.id, _FINAL_APPROVE, flow_users.admin)
        assert (second.status, second.final_reviewed_by, second.final_reviewed_at) == (
            "待补收货地址",
            flow_users.admin.id,
            later,
        )
        # 不写意见时存 null；第一级的不动
        assert (second.final_review_opinion, second.review_opinion) == (None, "价格合适")
        assert second.final_reviewer_name is not None
        assert second.promotion_id is None
        assert await _promotion_count(session, line_id) == 0

        events = await _events(session, n.id)
        assert [(e[0], e[1], e[2], e[4], e[5], e[6]) for e in events] == [
            (
                "negotiation.manager_approved",
                "主管待审",
                "待老板审核",
                flow_users.pr_manager.id,
                line_id,
                "价格合适",
            ),
            (
                "negotiation.final_approved",
                "待老板审核",
                "待补收货地址",
                flow_users.admin.id,
                line_id,
                None,
            ),
        ]

    async def test_manager_reject_is_terminal(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """主管驳回 = 终态「审核驳回」，意见留在行上（博主卡片读它）；ui 只剩复制。"""
        n = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        resp = await svc.review(
            n.id,
            NegotiationReviewRequest(
                action=NegotiationReviewAction.REJECT, review_opinion="报价高"
            ),
            flow_users.pr_manager,
        )
        assert (resp.status, resp.review_opinion, resp.promotion_id) == ("审核驳回", "报价高", None)
        events = await _events(session, n.id)
        assert [(e[0], e[2], e[6]) for e in events] == [
            ("negotiation.manager_rejected", "审核驳回", "报价高")
        ]
        own = await svc.get(n.id, flow_users.pr)
        assert own.ui is not None
        assert set(own.ui["actions"]) == {"copy"}

    async def test_final_return(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """老板退回 → 老板退回（PR 能改、能重提），退回意见在行上与事件 note 里。"""
        n = await world.make(
            flow_users.pr, "待老板审核", reviewed_by=flow_users.pr_manager.id, review_opinion="可以"
        )
        resp = await NegotiationService(session).final_review(
            n.id,
            NegotiationFinalReviewRequest(
                action=NegotiationFinalReviewAction.RETURN, opinion="价格再压一压"
            ),
            flow_users.admin,
        )
        assert (resp.status, resp.final_review_opinion, resp.review_opinion) == (
            "老板退回",
            "价格再压一压",
            "可以",
        )
        events = await _events(session, n.id)
        assert [(e[0], e[2], e[4], e[6]) for e in events] == [
            ("negotiation.final_returned", "老板退回", flow_users.admin.id, "价格再压一压")
        ]
        own = await NegotiationService(session).get(n.id, flow_users.pr)
        assert own.ui is not None
        assert own.ui["actions"]["edit"] == {"state": "enabled"}

    async def test_opinion_required_in_schema(self) -> None:
        """驳回 / 退回必须写意见（schema 层，只有空白也不行）；通过 / 定稿可以不写。"""
        with pytest.raises(PydanticValidationError):
            NegotiationReviewRequest(action=NegotiationReviewAction.REJECT, review_opinion="  ")
        with pytest.raises(PydanticValidationError):
            NegotiationFinalReviewRequest(action=NegotiationFinalReviewAction.RETURN)
        with pytest.raises(PydanticValidationError):
            NegotiationFinalReviewRequest(action=NegotiationFinalReviewAction.RETURN, opinion=" ")
        assert _FINAL_APPROVE.opinion is None


# ---------------------------------------------------------------------------
# S7 审核人规则（3.2 Q3；9.3 B「审核人规则」）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReviewerRules:
    async def test_admin_cannot_do_first_level(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """管理员（持 ``*``）做第一级 → 403 ``star_first_level``，单不动。"""
        n = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        with pytest.raises(FlowActionForbiddenError) as exc:
            await svc.review(n.id, _APPROVE, flow_users.admin)
        assert exc.value.code == "FLOW_ACTION_FORBIDDEN"
        assert exc.value.details == {
            "rule": "star_first_level",
            "reason": "第一级由 PR 主管审核，管理员不能代做",
        }
        assert (await svc.get(n.id, flow_users.pr)).status == "主管待审"
        assert await _events(session, n.id) == []

    async def test_manager_cannot_review_own(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """主管审自己建的 → 403 现有自审 code；主管自建的由管理员定稿不受影响（第一级要临时授权代理人，11-24）。"""
        n = await world.make(flow_users.pr_manager, "主管待审")
        svc = NegotiationService(session)
        with pytest.raises(NegotiationSelfReviewForbiddenError) as exc:
            await svc.review(n.id, _APPROVE, flow_users.pr_manager)
        assert (exc.value.code, exc.value.status_code) == ("NEGOTIATION_SELF_REVIEW_FORBIDDEN", 403)
        assert exc.value.details["rule"] == "self_review"
        assert (await svc.get(n.id, flow_users.pr_manager)).status == "主管待审"

    async def test_same_person_cannot_pass_both_levels(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        """给主管个人授 ``negotiation_final:approve`` 后两级都由他做 → 403 ``same_reviewer``；别人过的第一级他能定稿。"""
        await _override(session, tenant_a, flow_users.pr_manager, "negotiation_final:approve")
        svc = NegotiationService(session)
        n = await world.make(flow_users.pr, "主管待审")
        passed = await svc.review(n.id, _APPROVE, flow_users.pr_manager)
        assert passed.status == "待老板审核"
        assert passed.ui is not None
        assert passed.ui["actions"]["final_review"] == {
            "state": "disabled",
            "reason": "两级审核不能由同一人完成",
        }
        with pytest.raises(FlowActionForbiddenError) as exc:
            await svc.final_review(n.id, _FINAL_APPROVE, flow_users.pr_manager)
        assert exc.value.details["rule"] == "same_reviewer"
        assert (await svc.get(n.id, flow_users.pr)).status == "待老板审核"

        other = await world.make(flow_users.pr, "待老板审核", reviewed_by=flow_users.admin.id)
        done = await svc.final_review(other.id, _FINAL_APPROVE, flow_users.pr_manager)
        assert done.status == "待补收货地址"
        # 老板一级同样不能审自己谈的
        own = await world.make(flow_users.pr_manager, "待老板审核", reviewed_by=flow_users.admin.id)
        with pytest.raises(NegotiationSelfReviewForbiddenError):
            await svc.final_review(own.id, _FINAL_APPROVE, flow_users.pr_manager)

    async def test_proxy_reviews_others_not_own(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        """主管请假的代理人（PR + 个人授 ``negotiation.review:approve`` 与 ``negotiation_all:read``）：能审别人的，不能审自己的。"""
        await _override(session, tenant_a, flow_users.pr, "negotiation.review:approve")
        await _override(session, tenant_a, flow_users.pr, "negotiation_all:read")
        svc = NegotiationService(session)
        theirs = await world.make(flow_users.pr2, "主管待审")
        mine = await world.make(flow_users.pr, "主管待审")
        resp = await svc.review(theirs.id, _APPROVE, flow_users.pr)
        assert (resp.status, resp.reviewed_by) == ("待老板审核", flow_users.pr.id)
        with pytest.raises(NegotiationSelfReviewForbiddenError):
            await svc.review(mine.id, _APPROVE, flow_users.pr)

    async def test_review_needs_visibility(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        """只授了 ``negotiation.review:approve`` 的代理人看不到别人的单：审 → 404（先于规则）。"""
        await _override(session, tenant_a, flow_users.pr, "negotiation.review:approve")
        theirs = await world.make(flow_users.pr2, "主管待审")
        with pytest.raises(NegotiationNotFoundError):
            await NegotiationService(session).review(theirs.id, _APPROVE, flow_users.pr)

    async def test_manager_lacks_final_scope(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """默认角色的主管没有老板权：service 层同样拦（403 ``PERMISSION_DENIED``，缺能力项不是规则）。"""
        n = await world.make(flow_users.pr, "待老板审核", reviewed_by=flow_users.admin.id)
        with pytest.raises(PermissionDeniedError) as exc:
            await NegotiationService(session).final_review(
                n.id, _FINAL_APPROVE, flow_users.pr_manager
            )
        assert exc.value.code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# S8 收货信息与定稿建单（N8、N9；9.3 B「并发防重复建单」「复制到推广单」「PR 不在时」）
# ---------------------------------------------------------------------------


def _confirm(**kw: Any) -> NegotiationReceiverConfirm:
    return NegotiationReceiverConfirm(**{**_RECEIVER, **kw})


async def _receiver_pending(world: _World, pr: Any, flow_users: Any, **kw: Any) -> Any:
    """待补收货地址（两级都过了）的谈款。"""
    kw.setdefault("reviewed_by", flow_users.pr_manager.id)
    kw.setdefault("final_reviewed_by", flow_users.admin.id)
    return await world.make(pr, "待补收货地址", **kw)


async def _promotion_row(session: AsyncSession, promotion_id: Any) -> Any:
    return (
        await session.execute(
            sa_text(
                "SELECT pr_id, line_id, ship_status, receiver_name, receiver_phone, receiver_address,"
                " sku_id, goods_main_id, cooperation_mode, platform, scheduled_publish_date,"
                " quote_amount, remark, cooperation_date FROM promotion WHERE id = :p"
            ),
            {"p": promotion_id},
        )
    ).one()


async def _promotion_items(session: AsyncSession, promotion_id: Any) -> list[tuple[Any, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT style_id, sku_id FROM promotion_item WHERE promotion_id = :p ORDER BY sort_order"
        ),
        {"p": promotion_id},
    )
    return [(r[0], r[1]) for r in rows.all()]


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverTransitions:
    """转移全表的收货两列（N8、N9）：只在待补收货地址；已定稿再确认 → 409「已定稿，推广单已生成」。"""

    @pytest.mark.parametrize("status", _STATES)
    async def test_receiver_save(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        body = NegotiationReceiverUpdate(receiver_name="李四")
        if status == "待补收货地址":
            resp = await svc.save_receiver(n.id, body, flow_users.pr)
            assert (resp.status, resp.receiver_name) == (status, "李四")
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.save_receiver(n.id, body, flow_users.pr)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "receiver_save"}

    @pytest.mark.parametrize("status", _STATES)
    async def test_receiver_confirm(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status == "待补收货地址":
            resp = await svc.confirm_receiver(n.id, _confirm(), flow_users.pr)
            assert resp.status == "已定稿"
            assert resp.promotion_id is not None
            return
        if status == "已定稿":
            with pytest.raises(NegotiationStateConflictError) as conflict:
                await svc.confirm_receiver(n.id, _confirm(), flow_users.pr)
            assert (conflict.value.code, conflict.value.status_code) == (
                "NEGOTIATION_STATE_CONFLICT",
                409,
            )
            assert conflict.value.message == FINALIZED_CONFLICT_MESSAGE
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.confirm_receiver(n.id, _confirm(), flow_users.pr)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "receiver_confirm"}


@pytest.mark.usefixtures("tenant_ctx")
class TestFinalize:
    async def test_copied_to_promotion(
        self,
        session: AsyncSession,
        flow_users: Any,
        world: _World,
        suit: _Suit,
        negotiation_factory: Any,
    ) -> None:
        """定稿建单（9.3 B「复制到推广单」）：建单人 = 谈款 PR、``line_id`` 同谈款、待发货、收件三项（电话规范化）、
        套装 2 行明细、``promotion.sku_id`` = 主款式那行、8 项照抄、合作日期 = 确认当天；事件 finalized + created。
        之后推送仓库不用再补选（B8：不复制明细 → 推送缺颜色尺码）；改推广单收件不回写谈款。
        """
        n = await negotiation_factory.negotiation(
            style=suit.top,
            blogger=world.blogger,
            pr=flow_users.pr,
            goods_main_id=suit.suit.id,
            status="待补收货地址",
            scheduled_publish_date=date(2026, 11, 1),
            quote_amount=Decimal("800.00"),
            remark="备注一句",
            submitted_at=_T0,
            reviewed_by=flow_users.pr_manager.id,
            final_reviewed_by=flow_users.admin.id,
            items=[(suit.top, suit.top_sku), (suit.pants, suit.pants_sku)],
        )
        line_id = n.line_id
        resp = await NegotiationService(session).confirm_receiver(
            n.id, _confirm(receiver_phone="+86 138-1234-5678"), flow_users.pr
        )
        assert resp.status == "已定稿"
        assert resp.promotion_id is not None and resp.promotion_internal_code
        assert (resp.receiver_confirmed_by, resp.receiver_phone) == (
            flow_users.pr.id,
            "13812345678",
        )
        assert resp.receiver_confirmed_at is not None
        row = await _promotion_row(session, resp.promotion_id)
        assert tuple(row) == (
            flow_users.pr.id,
            line_id,
            "待发货",
            "张三",
            "13812345678",
            _RECEIVER["receiver_address"],
            suit.top_sku.id,
            suit.suit.id,
            "寄拍",
            "小红书",
            date(2026, 11, 1),
            Decimal("800.00"),
            "备注一句",
            get_today(),
        )
        assert await _promotion_items(session, resp.promotion_id) == [
            (suit.top.id, suit.top_sku.id),
            (suit.pants.id, suit.pants_sku.id),
        ]
        events = (
            await session.execute(
                sa_text(
                    "SELECT event_type, negotiation_id, promotion_id, from_state, to_state,"
                    " actor_id, payload FROM line_event WHERE line_id = :l ORDER BY seq"
                ),
                {"l": line_id},
            )
        ).all()
        assert [tuple(e) for e in events] == [
            (
                "promotion.created",
                None,
                resp.promotion_id,
                None,
                None,
                flow_users.pr.id,
                {"source": "谈款定稿", "negotiation_id": str(n.id)},
            ),
            (
                "negotiation.finalized",
                n.id,
                None,
                "待补收货地址",
                "已定稿",
                flow_users.pr.id,
                {"promotion_id": str(resp.promotion_id)},
            ),
        ]
        promotions = PromotionService(session)
        await promotions.update_promotion(
            resp.promotion_id, PromotionUpdate(receiver_address="上海市某路 2 号"), flow_users.pr
        )
        assert (await NegotiationService(session).get(n.id, flow_users.pr)).receiver_address == (
            _RECEIVER["receiver_address"]
        )
        pushed = await promotions.ship_push(
            resp.promotion_id, PromotionShipPushRequest(), flow_users.pr_manager
        )
        assert pushed.ship_status == "待打单"

    async def test_body_wins_and_gates_reported_together(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """以 body 为准（与已保存的不同也行）；三项缺 / 电话格式不对一次全报，键与 ``ui`` 相同，什么都不写。"""
        n = await _receiver_pending(
            world, flow_users.pr, flow_users, receiver_name="旧名字", receiver_phone="13800000000"
        )
        svc = NegotiationService(session)
        ui_missing = [
            m["key"]
            for m in ((await svc.get(n.id, flow_users.pr)).ui or {})["actions"]["receiver_confirm"][
                "missing"
            ]
        ]
        assert ui_missing == ["receiver_address"]
        with pytest.raises(FlowGateMissingError) as exc:
            await svc.confirm_receiver(
                n.id,
                NegotiationReceiverConfirm(
                    receiver_name="  ", receiver_phone="12345", receiver_address=None
                ),
                flow_users.pr,
            )
        assert [m["key"] for m in exc.value.details["missing"]] == [
            "receiver_name",
            "receiver_phone",
            "receiver_address",
        ]
        assert (await svc.get(n.id, flow_users.pr)).status == "待补收货地址"
        assert await _promotion_count(session, n.line_id) == 0
        done = await svc.confirm_receiver(n.id, _confirm(receiver_name="新名字"), flow_users.pr)
        assert (done.receiver_name, done.receiver_phone) == ("新名字", "13812345678")
        row = await _promotion_row(session, done.promotion_id)
        assert (row[3], row[4]) == ("新名字", "13812345678")

    async def test_items_not_copied_when_incomplete(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """存量没选颜色尺码的（偏差 5）：照样定稿建单，明细不复制，推送仓库时补选。"""
        n = await _receiver_pending(world, flow_users.pr, flow_users, items=[(world.style, None)])
        resp = await NegotiationService(session).confirm_receiver(n.id, _confirm(), flow_users.pr)
        assert resp.status == "已定稿"
        assert await _promotion_items(session, resp.promotion_id) == []
        assert (await _promotion_row(session, resp.promotion_id))[6] is None

    async def test_confirm_twice(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await _receiver_pending(world, flow_users.pr, flow_users)
        svc = NegotiationService(session)
        first = await svc.confirm_receiver(n.id, _confirm(), flow_users.pr)
        with pytest.raises(NegotiationStateConflictError) as exc:
            await svc.confirm_receiver(n.id, _confirm(), flow_users.pr_manager)
        assert exc.value.details["promotion_id"] == str(first.promotion_id)
        assert await _promotion_count(session, n.line_id) == 1


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverSave:
    async def test_save_normalizes_and_records_diff(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """只改传了的项；电话规范化后存；事件 receiver_saved 带 diff；没有改动不写事件。"""
        n = await _receiver_pending(world, flow_users.pr, flow_users)
        svc = NegotiationService(session)
        first = await svc.save_receiver(
            n.id,
            NegotiationReceiverUpdate(receiver_name=" 张三 ", receiver_phone="138 1234 5678"),
            flow_users.pr,
        )
        assert (first.status, first.receiver_name, first.receiver_phone) == (
            "待补收货地址",
            "张三",
            "13812345678",
        )
        await svc.save_receiver(
            n.id, NegotiationReceiverUpdate(receiver_address="杭州某路"), flow_users.pr
        )
        await svc.save_receiver(
            n.id, NegotiationReceiverUpdate(receiver_name="张三"), flow_users.pr
        )
        cleared = await svc.save_receiver(
            n.id, NegotiationReceiverUpdate(receiver_phone=""), flow_users.pr
        )
        assert (cleared.receiver_name, cleared.receiver_phone, cleared.receiver_address) == (
            "张三",
            None,
            "杭州某路",
        )
        events = [e for e in await _events(session, n.id) if e[0] == "negotiation.receiver_saved"]
        assert [(e[3], e[4]) for e in events] == [
            (
                {
                    "diff": {
                        "receiver_name": {"before": None, "after": "张三"},
                        "receiver_phone": {"before": None, "after": "13812345678"},
                    }
                },
                flow_users.pr.id,
            ),
            (
                {"diff": {"receiver_address": {"before": None, "after": "杭州某路"}}},
                flow_users.pr.id,
            ),
            (
                {"diff": {"receiver_phone": {"before": "13812345678", "after": None}}},
                flow_users.pr.id,
            ),
        ]

    async def test_bad_phone_and_length(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        n = await _receiver_pending(world, flow_users.pr, flow_users)
        with pytest.raises(InvalidReceiverPhoneError):
            await NegotiationService(session).save_receiver(
                n.id, NegotiationReceiverUpdate(receiver_phone="12345"), flow_users.pr
            )
        assert [
            e for e in await _events(session, n.id) if e[0] == "negotiation.receiver_saved"
        ] == []
        for field, size in (
            ("receiver_name", 33),
            ("receiver_phone", 33),
            ("receiver_address", 256),
        ):
            with pytest.raises(PydanticValidationError):
                NegotiationReceiverUpdate(**{field: "1" * size})
            with pytest.raises(PydanticValidationError):
                NegotiationReceiverConfirm(**{field: "1" * size})


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverProxy:
    """PR 不在时（Q4、11-57）：主管、代理人、管理员能代录、代确认；确认人另记，建单人仍是谈款 PR。"""

    async def test_manager_admin_proxy_confirm_for_pr(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        await _override(session, tenant_a, flow_users.pr2, "negotiation.review:approve")
        await _override(session, tenant_a, flow_users.pr2, "negotiation_all:read")
        svc = NegotiationService(session)
        for who in ("pr_manager", "admin", "pr2"):
            actor = getattr(flow_users, who)
            n = await _receiver_pending(world, flow_users.pr, flow_users)
            saved = await svc.save_receiver(
                n.id, NegotiationReceiverUpdate(receiver_name=f"{who} 代录"), actor
            )
            assert saved.receiver_name == f"{who} 代录", who
            done = await svc.confirm_receiver(n.id, _confirm(), actor)
            assert (done.status, done.receiver_confirmed_by, done.pr_id) == (
                "已定稿",
                actor.id,
                flow_users.pr.id,
            ), who
            row = await _promotion_row(session, done.promotion_id)
            assert row[0] == flow_users.pr.id, who
            finalized = [e for e in await _events(session, n.id) if e[0] == "negotiation.finalized"]
            assert [e[4] for e in finalized] == [actor.id], who

    async def test_other_pr_not_owner(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any, world: _World
    ) -> None:
        """别的 PR：看不到 → 404；只授了看全部（没有主管审核权）→ 403 ``not_owner``。财务看得到但没有写权限 → 403。"""
        n = await _receiver_pending(world, flow_users.pr, flow_users)
        svc = NegotiationService(session)
        with pytest.raises(NegotiationNotFoundError):
            await svc.save_receiver(
                n.id, NegotiationReceiverUpdate(receiver_name="x"), flow_users.pr2
            )
        await _override(session, tenant_a, flow_users.pr2, "negotiation_all:read")
        for call in (
            svc.save_receiver(n.id, NegotiationReceiverUpdate(receiver_name="x"), flow_users.pr2),
            svc.confirm_receiver(n.id, _confirm(), flow_users.pr2),
        ):
            with pytest.raises(FlowActionForbiddenError) as exc:
                await call
            assert exc.value.details["rule"] == "not_owner"
        with pytest.raises(PermissionDeniedError) as denied:
            await svc.confirm_receiver(n.id, _confirm(), flow_users.finance)
        assert denied.value.code == "PERMISSION_DENIED"
        assert (await svc.get(n.id, flow_users.pr)).status == "待补收货地址"
        assert await _promotion_count(session, n.line_id) == 0


# ---------------------------------------------------------------------------
# S8 并发：30 条独立连接同时确认同一张 → 恰好 1 张推广单、29 个 409、0 个别的错（9.3 B「并发防重复建单」）
# ---------------------------------------------------------------------------


class TestConfirmConcurrent:
    async def test_30_concurrent_confirms(self, engine: Any) -> None:
        from app.core.security.auth import hash_password

        tenant_id = await default_tenant_id(engine)
        today = get_today()
        suffix = uuid4().hex[:8]
        negotiation_id, line_id = uuid4(), uuid4()
        style_id, sku_id, blogger_id = uuid4(), uuid4(), uuid4()
        codes = ("pr", "pr_manager", "admin")  # 本人、主管、管理员轮流确认
        user_ids = {code: uuid4() for code in codes}
        async with committed(engine) as seed:
            seq_before = (
                await seed.execute(
                    sa_text(
                        "SELECT last_seq FROM promotion_sequence"
                        " WHERE tenant_id = :tid AND date_key = :dk"
                    ),
                    {"tid": tenant_id, "dk": today},
                )
            ).scalar_one_or_none()
            for code, uid in user_ids.items():
                await seed.execute(
                    sa_text(
                        'INSERT INTO "user" (id, tenant_id, username, password_hash, status, '
                        "password_must_change, created_at, updated_at) "
                        "VALUES (:id, :tid, :un, :ph, 'active', false, NOW(), NOW())"
                    ),
                    {
                        "id": uid,
                        "tid": tenant_id,
                        "un": f"neg_{code}_{suffix}",
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
                    "VALUES (:id, :tid, :code, '并发谈款款', '连衣裙', '大货', true, false, NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "code": f"NEGC{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO sku (id, tenant_id, style_id, sku_code, color, size, base_price, "
                    "sourcing_type, is_active, is_deleted, created_at, updated_at) VALUES (:id, "
                    ":tid, :sid, :code, '黑色', 'M', 200, '自产', true, false, NOW(), NOW())"
                ),
                {"id": sku_id, "tid": tenant_id, "sid": style_id, "code": f"NEGK{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :xhs, '并发谈款博主', '小红书', false, true, false, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "xhs": f"XHSN{suffix}"},
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO negotiation (id, tenant_id, line_id, blogger_id, style_id, pr_id, "
                    "cooperation_mode, platform, scheduled_publish_date, quote_amount, status, "
                    "submitted_at, reviewed_by, final_reviewed_by, created_at, updated_at) VALUES "
                    "(:id, :tid, :line, :bid, :sid, :pr, '寄拍', '小红书', :spd, 500.00, "
                    "'待补收货地址', NOW(), :mgr, :adm, NOW(), NOW())"
                ),
                {
                    "id": negotiation_id,
                    "tid": tenant_id,
                    "line": line_id,
                    "bid": blogger_id,
                    "sid": style_id,
                    "pr": user_ids["pr"],
                    "spd": date(2026, 11, 1),
                    "mgr": user_ids["pr_manager"],
                    "adm": user_ids["admin"],
                },
            )
            await seed.execute(
                sa_text(
                    "INSERT INTO negotiation_item (id, tenant_id, negotiation_id, style_id, sku_id, "
                    "sort_order, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tid, :nid, :sid, :sku, 0, NOW(), NOW())"
                ),
                {"tid": tenant_id, "nid": negotiation_id, "sid": style_id, "sku": sku_id},
            )

        n = 30
        barrier = asyncio.Barrier(n)

        async def attempt(s: AsyncSession, i: int) -> str:
            user = await s.get(User, user_ids[codes[i % len(codes)]])  # 先拿到连接，再一起出发
            assert user is not None
            await barrier.wait()
            try:
                await NegotiationService(s).confirm_receiver(negotiation_id, _confirm(), user)
                return "ok"
            except NegotiationStateConflictError as exc:
                return "conflict" if exc.message == FINALIZED_CONFLICT_MESSAGE else "conflict?"
            except Exception as e:
                return f"other:{type(e).__name__}:{getattr(e, 'code', '')}"

        try:
            results = await run_concurrently(engine, n, attempt, tenant_id=tenant_id)
            assert results.count("ok") == 1, results
            assert results.count("conflict") == n - 1, results
            winner = user_ids[codes[results.index("ok") % len(codes)]]
            async with committed(engine) as check:
                by_line = (
                    await check.execute(
                        sa_text("SELECT id, pr_id FROM promotion WHERE line_id = :l"),
                        {"l": line_id},
                    )
                ).all()
                by_blogger = (
                    await check.execute(
                        sa_text("SELECT COUNT(*) FROM promotion WHERE blogger_id = :b"),
                        {"b": blogger_id},
                    )
                ).scalar_one()
                neg = (
                    await check.execute(
                        sa_text(
                            "SELECT status, promotion_id, receiver_confirmed_by FROM negotiation"
                            " WHERE id = :id"
                        ),
                        {"id": negotiation_id},
                    )
                ).one()
                finalized = (
                    await check.execute(
                        sa_text(
                            "SELECT COUNT(*) FROM line_event WHERE line_id = :l"
                            " AND event_type IN ('negotiation.finalized', 'promotion.created')"
                        ),
                        {"l": line_id},
                    )
                ).scalar_one()
            assert len(by_line) == 1 and by_blogger == 1, (by_line, by_blogger)
            assert by_line[0][1] == user_ids["pr"]
            assert tuple(neg) == ("已定稿", by_line[0][0], winner)
            assert finalized == 2
        finally:
            async with committed(engine) as cleanup:
                promotion_ids = list(
                    (
                        await cleanup.execute(
                            sa_text("SELECT id FROM promotion WHERE blogger_id = :b"),
                            {"b": blogger_id},
                        )
                    ).scalars()
                )
                await purge_negotiations(cleanup, [negotiation_id])
                from tests.conftest import purge_promotions

                await purge_promotions(cleanup, promotion_ids)
                await cleanup.execute(
                    sa_text("DELETE FROM audit_log WHERE resource_id = ANY(CAST(:r AS text[]))"),
                    {"r": [str(negotiation_id), *(str(p) for p in promotion_ids)]},
                )
                if seq_before is None:
                    await cleanup.execute(
                        sa_text(
                            "DELETE FROM promotion_sequence WHERE tenant_id = :tid AND date_key = :dk"
                        ),
                        {"tid": tenant_id, "dk": today},
                    )
                else:
                    await cleanup.execute(
                        sa_text(
                            "UPDATE promotion_sequence SET last_seq = :seq"
                            " WHERE tenant_id = :tid AND date_key = :dk"
                        ),
                        {"seq": seq_before, "tid": tenant_id, "dk": today},
                    )
                for sql, key in (
                    ("DELETE FROM sku WHERE id = :id", sku_id),
                    ("DELETE FROM style WHERE id = :id", style_id),
                    ("DELETE FROM blogger WHERE id = :id", blogger_id),
                ):
                    await cleanup.execute(sa_text(sql), {"id": key})
                for uid in user_ids.values():
                    await cleanup.execute(
                        sa_text("DELETE FROM user_role WHERE user_id = :id"), {"id": uid}
                    )
                    await cleanup.execute(sa_text('DELETE FROM "user" WHERE id = :id'), {"id": uid})


# ---------------------------------------------------------------------------
# S9 复制为新谈款（N10）与管理员转交（N12，11-57）
# ---------------------------------------------------------------------------

_REASSIGNABLE = ("主管待审", "待老板审核", "老板退回", "待补收货地址")


def _reassign(pr: Any, reason: str = "原 PR 请假") -> NegotiationReassignRequest:
    return NegotiationReassignRequest(pr_id=pr.id, reason=reason)


@pytest.mark.usefixtures("tenant_ctx")
class TestCopyReassignTransitions:
    """转移全表的复制 / 转交两列：复制只在审核驳回；转交只在 N12 的 4 个状态（草稿取不到 → 404）。"""

    @pytest.mark.parametrize("status", _STATES)
    async def test_copy(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status == "审核驳回":
            resp = await svc.copy(n.id, flow_users.pr)
            assert (resp.status, resp.copied_from_id) == ("草稿", n.id)
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.copy(n.id, flow_users.pr)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "copy"}

    @pytest.mark.parametrize("status", _STATES)
    async def test_reassign(
        self, session: AsyncSession, flow_users: Any, world: _World, status: str
    ) -> None:
        n = await world.make(flow_users.pr, status)
        svc = NegotiationService(session)
        if status == "草稿":
            with pytest.raises(NegotiationNotFoundError):
                await svc.reassign(n.id, _reassign(flow_users.pr2), flow_users.admin)
            return
        if status in _REASSIGNABLE:
            resp = await svc.reassign(n.id, _reassign(flow_users.pr2), flow_users.admin)
            assert (resp.status, resp.pr_id) == (status, flow_users.pr2.id)
            return
        with pytest.raises(IllegalStateTransitionError) as exc:
            await svc.reassign(n.id, _reassign(flow_users.pr2), flow_users.admin)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.details == {"from_state": status, "action": "reassign"}


@pytest.mark.usefixtures("tenant_ctx")
class TestCopy:
    async def test_copies_terms_not_reviews(
        self,
        session: AsyncSession,
        flow_users: Any,
        negotiation_factory: Any,
        blogger_factory: Any,
        suit: _Suit,
    ) -> None:
        """复制博主、商品、明细、模式、平台、服务费、备注、约定发布时间；不复制审核与收货。
        新单新 ``line_id``、``copied_from_id`` 指回原单、本人是新单 PR；两条主线各写一条事件互相引用。"""
        blogger = await blogger_factory.blogger()
        src = await negotiation_factory.negotiation(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            status="审核驳回",
            goods_main_id=suit.suit.id,
            items=[(suit.top, suit.top_sku), (suit.pants, None)],
            cooperation_mode="送拍",
            platform="抖音",
            scheduled_publish_date=date(2026, 12, 1),
            quote_amount=Decimal("888.00"),
            remark="上次价格太高",
            submitted_at=_T0,
            reviewed_by=flow_users.pr_manager.id,
            reviewed_at=_T0,
            review_opinion="价格太高",
            receiver_name="不该复制",
        )
        src_id, src_line = src.id, src.line_id
        svc = NegotiationService(session)
        new = await svc.copy(src_id, flow_users.pr)
        assert new.id != src_id and new.line_id != src_line
        assert (new.status, new.copied_from_id, new.pr_id) == ("草稿", src_id, flow_users.pr.id)
        assert (
            new.blogger_id,
            new.style_id,
            new.goods_main_id,
            new.cooperation_mode,
            new.platform,
            new.scheduled_publish_date,
            new.quote_amount,
            new.remark,
        ) == (
            blogger.id,
            suit.top.id,
            suit.suit.id,
            "送拍",
            "抖音",
            date(2026, 12, 1),
            Decimal("888.00"),
            "上次价格太高",
        )
        assert [(i.style_id, i.sku_id) for i in new.items] == [
            (suit.top.id, suit.top_sku.id),
            (suit.pants.id, None),
        ]
        assert (new.submitted_at, new.reviewed_by, new.review_opinion) == (None, None, None)
        assert (new.final_reviewed_by, new.receiver_name, new.promotion_id) == (None, None, None)

        assert [(e[0], e[1], e[2], e[3], e[4], e[5]) for e in await _events(session, new.id)] == [
            (
                "negotiation.copied",
                None,
                "草稿",
                {"from_negotiation_id": str(src_id), "from_line_id": str(src_line)},
                flow_users.pr.id,
                new.line_id,
            )
        ]
        assert [(e[0], e[1], e[2], e[3], e[4], e[5]) for e in await _events(session, src_id)] == [
            (
                "negotiation.copied_out",
                None,
                None,
                {"to_negotiation_id": str(new.id), "to_line_id": str(new.line_id)},
                flow_users.pr.id,
                src_line,
            )
        ]
        # 原单不动：仍是审核驳回、意见还在
        old = await svc.get(src_id, flow_users.pr)
        assert (old.status, old.review_opinion) == ("审核驳回", "价格太高")
        # 新草稿就是普通草稿：本人能改、能提交（颜色尺码没选齐 → 缺项）
        with pytest.raises(FlowGateMissingError) as gate:
            await svc.submit(new.id, flow_users.pr)
        assert [m["key"] for m in gate.value.details["missing"]] == ["goods_items"]

    async def test_only_owner_copies(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """主管、管理员看得到驳回单但不是本人 → 403 ``not_owner``；别的 PR 看不到 → 404；财务没有写权限 → 403。"""
        n = await world.make(flow_users.pr, "审核驳回")
        svc = NegotiationService(session)
        for who in ("pr_manager", "admin"):
            with pytest.raises(FlowActionForbiddenError) as exc:
                await svc.copy(n.id, getattr(flow_users, who))
            assert exc.value.details["rule"] == "not_owner", who
        with pytest.raises(NegotiationNotFoundError):
            await svc.copy(n.id, flow_users.pr2)
        with pytest.raises(PermissionDeniedError) as denied:
            await svc.copy(n.id, flow_users.finance)
        assert denied.value.code == "PERMISSION_DENIED"
        copies = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM negotiation WHERE copied_from_id = :n"), {"n": n.id}
            )
        ).scalar_one()
        assert copies == 0


@pytest.mark.usefixtures("tenant_ctx")
class TestReassign:
    async def test_reassign_moves_ownership(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """转交后新 PR 能编辑、提交，原 PR 列表里看不到、编辑 404；事件带前后 PR 与原因，状态不变。"""
        n = await world.make(
            flow_users.pr, "老板退回", reviewed_by=flow_users.pr_manager.id, final_reviewed_by=None
        )
        nid = n.id
        svc = NegotiationService(session)
        before_name = flow_users.pr.display_name or flow_users.pr.username
        after_name = flow_users.pr2.display_name or flow_users.pr2.username
        resp = await svc.reassign(
            nid, _reassign(flow_users.pr2, "  原 PR 离职  "), flow_users.admin
        )
        assert (resp.status, resp.pr_id) == ("老板退回", flow_users.pr2.id)
        events = [e for e in await _events(session, nid) if e[0] == "negotiation.reassigned"]
        assert [(e[1], e[2], e[3], e[4], e[6]) for e in events] == [
            (
                None,
                None,
                {
                    "from_pr_id": str(flow_users.pr.id),
                    "from_pr_name": before_name,
                    "to_pr_id": str(flow_users.pr2.id),
                    "to_pr_name": after_name,
                },
                flow_users.admin.id,
                "原 PR 离职",
            )
        ]
        assert nid not in await _ids(svc, flow_users.pr)
        assert nid in await _ids(svc, flow_users.pr2)
        with pytest.raises(NegotiationNotFoundError):
            await svc.update(nid, NegotiationUpdate(remark="原 PR"), flow_users.pr)
        edited = await svc.update(nid, NegotiationUpdate(remark="新 PR 改"), flow_users.pr2)
        assert edited.remark == "新 PR 改"
        submitted = await svc.submit(nid, flow_users.pr2)
        assert submitted.status == "主管待审"
        # 之后两级审核的「≠ 谈款人」按新 PR 判：原 PR 不再是谈款人
        assert submitted.pr_id == flow_users.pr2.id

    async def test_new_pr_must_qualify(
        self, session: AsyncSession, tenant_a: Any, factory: Any, flow_users: Any, world: _World
    ) -> None:
        """新 PR 要是启用账号、持 ``negotiation:write``、≠ 原 PR、≠ 本轮两级审核人（否则等于自审），也不能是持 ``*`` 的账号
        （管理员建的谈款没人能定稿，同 N1）→ 都是 422 ``INVALID_NEGOTIATION_ASSIGNEE``，谈款不动。"""
        disabled = await factory.user(
            tenant_a, roles=[await _role(session, "pr")], status="disabled"
        )
        n = await world.make(
            flow_users.pr,
            "待老板审核",
            reviewed_by=flow_users.pr_manager.id,
            final_reviewed_by=flow_users.pr2.id,
        )
        svc = NegotiationService(session)
        cases = {
            "same_pr": flow_users.pr.id,
            "manager_reviewer": flow_users.pr_manager.id,
            "final_reviewer": flow_users.pr2.id,
            "star": flow_users.admin.id,
            "no_write": flow_users.finance.id,
            "disabled": disabled.id,
            "missing": uuid4(),
        }
        for name, pr_id in cases.items():
            with pytest.raises(AppException) as exc:
                await svc.reassign(
                    n.id, NegotiationReassignRequest(pr_id=pr_id, reason="转"), flow_users.admin
                )
            assert (exc.value.code, exc.value.status_code) == (
                "INVALID_NEGOTIATION_ASSIGNEE",
                422,
            ), name
            assert exc.value.details["pr_id"] == str(pr_id), name
        assert (await svc.get(n.id, flow_users.admin)).pr_id == flow_users.pr.id
        reassigned = (
            await session.execute(
                sa_text(
                    "SELECT COUNT(*) FROM line_event WHERE negotiation_id = :n"
                    " AND event_type = 'negotiation.reassigned'"
                ),
                {"n": n.id},
            )
        ).scalar_one()
        assert reassigned == 0

    async def test_manager_reviewer_rejected(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """转给本轮主管审核人 → 422（9.3 B「PR 不在时」；B12 去掉「≠ 本轮审核人」这条会红）。
        主管也持 ``negotiation:write``，所以只有这条规则挡得住。"""
        n = await world.make(flow_users.pr, "待老板审核", reviewed_by=flow_users.pr_manager.id)
        svc = NegotiationService(session)
        with pytest.raises(AppException) as exc:
            await svc.reassign(n.id, _reassign(flow_users.pr_manager), flow_users.admin)
        assert exc.value.code == "INVALID_NEGOTIATION_ASSIGNEE"
        assert (await svc.get(n.id, flow_users.admin)).pr_id == flow_users.pr.id

    async def test_admin_only(self, session: AsyncSession, flow_users: Any, world: _World) -> None:
        """不持 ``*`` → 403 ``admin_only``（主管、本人、别的 PR 都一样）；转交不进矩阵，``ui`` 里没有这个动作。"""
        n = await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        for who in ("pr_manager", "pr"):
            with pytest.raises(FlowActionForbiddenError) as exc:
                await svc.reassign(n.id, _reassign(flow_users.pr2), getattr(flow_users, who))
            assert exc.value.details["rule"] == "admin_only", who
        detail = await svc.get(n.id, flow_users.admin)
        assert "reassign" not in (detail.ui or {}).get("actions", {})
        assert detail.pr_id == flow_users.pr.id


async def _role(session: AsyncSession, code: str) -> Any:
    from app.modules.auth.models import Role

    return (await session.execute(select(Role).where(Role.code == code))).scalar_one()


# ---------------------------------------------------------------------------
# S10 博主卡片「谈款驳回记录」（7c-7）与「同博主有别的 PR 在谈」（7c-16）
# ---------------------------------------------------------------------------
async def _rejected(world: _World, pr: Any, at: datetime, **kw: Any) -> Any:
    kw.setdefault("reviewed_at", at)
    kw.setdefault("review_opinion", f"驳回 {at:%H:%M}")
    return await world.make(pr, "审核驳回", **kw)


@pytest.mark.usefixtures("tenant_ctx")
class TestBloggerRejections:
    async def test_cross_pr_and_field_rules(
        self,
        session: AsyncSession,
        flow_users: Any,
        world: _World,
        blogger_factory: Any,
        negotiation_factory: Any,
    ) -> None:
        """所有 PR 的主管驳回单都进卡片（不按 7c-10 过滤）；老板退回、别的博主的不进；
        ``negotiation_id`` 只给看得到的人；服务费照 ``promotion.quote_amount`` 字段规则（运营 null）。"""
        mine = await _rejected(
            world,
            flow_users.pr,
            _T0,
            reviewed_by=flow_users.pr_manager.id,
            review_opinion="报价太高",
            platform="抖音",
            quote_amount=Decimal("800.00"),
        )
        theirs = await _rejected(
            world, flow_users.pr2, _T0 + timedelta(hours=1), review_opinion="档期冲突"
        )
        await world.make(
            flow_users.pr2,
            "老板退回",
            reviewed_by=flow_users.pr_manager.id,
            final_review_opinion="老板退回不进卡片",
        )
        other_blogger = await blogger_factory.blogger()
        await negotiation_factory.negotiation(
            style=world.style,
            blogger=other_blogger,
            pr=flow_users.pr,
            status="审核驳回",
            reviewed_at=_T0,
        )
        svc = NegotiationService(session)
        pr_name = flow_users.pr.display_name or flow_users.pr.username
        pr2_name = flow_users.pr2.display_name or flow_users.pr2.username

        hist = await svc.blogger_history(world.blogger.id, flow_users.pr)
        assert hist.total_rejections == 2
        assert [r.rejected_at for r in hist.rejections] == [_T0 + timedelta(hours=1), _T0]
        assert [r.model_dump(mode="json", exclude={"rejected_at"}) for r in hist.rejections] == [
            {
                "negotiation_id": None,  # 别的 PR 的单，PR 看不到 → 不给 id
                "pr_name": pr2_name,
                "style_code": world.style.style_code,
                "display_short_name": "飞狐短裙",
                "platform": "小红书",
                "quote_amount": "500.00",
                "opinion": "档期冲突",
            },
            {
                "negotiation_id": str(mine.id),
                "pr_name": pr_name,
                "style_code": world.style.style_code,
                "display_short_name": "飞狐短裙",
                "platform": "抖音",
                "quote_amount": "800.00",
                "opinion": "报价太高",
            },
        ]
        # 看全部的人（主管、财务、运营、管理员）两张都给 id；运营没有服务费读权限
        for who in ("pr_manager", "finance", "operations", "admin"):
            hist = await svc.blogger_history(world.blogger.id, getattr(flow_users, who))
            assert [r.negotiation_id for r in hist.rejections] == [theirs.id, mine.id], who
            quotes = [r.quote_amount for r in hist.rejections]
            if who == "operations":
                assert quotes == [None, None], who
            else:
                assert quotes == [Decimal("500.00"), Decimal("800.00")], who
        # PR 乙看甲的那张也没有 id，自己的有
        hist = await svc.blogger_history(world.blogger.id, flow_users.pr2)
        assert [r.negotiation_id for r in hist.rejections] == [theirs.id, None]

    async def test_latest_five_and_total(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """最多 5 条、按驳回时间倒序；``total_rejections`` 不受 5 条限制；合作的 ``limit`` 不影响驳回条数。"""
        made = [
            await _rejected(
                world, (flow_users.pr, flow_users.pr2)[i % 2], _T0 + timedelta(minutes=i)
            )
            for i in range(7)
        ]
        hist = await NegotiationService(session).blogger_history(
            world.blogger.id, flow_users.pr_manager, limit=1
        )
        assert hist.total_rejections == 7
        assert [r.negotiation_id for r in hist.rejections] == [n.id for n in made[::-1][:5]]

    async def test_no_rejections(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        await world.make(flow_users.pr, "主管待审")
        hist = await NegotiationService(session).blogger_history(world.blogger.id, flow_users.pr)
        assert (hist.rejections, hist.total_rejections) == ([], 0)


@pytest.mark.usefixtures("tenant_ctx")
class TestOthersInProgress:
    async def test_only_others_in_progress(
        self, session: AsyncSession, flow_users: Any, world: _World
    ) -> None:
        """别的 PR 在谈（5 个在谈状态）的单跨人可见，只有姓名、状态、更新时间；自己的、终态的不算；
        ``exclude_negotiation_id`` 排除指定那张。"""
        in_progress = {
            status: await world.make(flow_users.pr2, status)
            for status in ("草稿", "主管待审", "待老板审核", "老板退回", "待补收货地址")
        }
        for status in ("审核驳回", "已定稿"):
            await world.make(flow_users.pr2, status)
        await world.make(flow_users.pr, "主管待审")
        svc = NegotiationService(session)
        rows = await svc.others_in_progress(world.blogger.id, flow_users.pr)
        pr2_name = flow_users.pr2.display_name or flow_users.pr2.username
        assert sorted(r.status for r in rows) == sorted(in_progress)
        assert {r.pr_name for r in rows} == {pr2_name}
        assert all(set(r.model_dump()) == {"pr_name", "status", "updated_at"} for r in rows)
        excluded = await svc.others_in_progress(
            world.blogger.id,
            flow_users.pr,
            exclude_negotiation_id=in_progress["老板退回"].id,
        )
        assert sorted(r.status for r in excluded) == sorted(set(in_progress) - {"老板退回"})
        # PR 乙看到的是甲那张；主管看到两人的
        assert [
            r.status for r in await svc.others_in_progress(world.blogger.id, flow_users.pr2)
        ] == ["主管待审"]
        assert len(await svc.others_in_progress(world.blogger.id, flow_users.pr_manager)) == 6
