"""流程线 PR-3：谈款两级审核（设计 3.2、7.2、9.3 B 块）。

本文件按条目逐步长：S5 可见范围与读接口（列表 / 计数 / 详情的可见范围、超 36 小时、``ui``、收件投影、
``receiver_suggestion``、商品明细与品名、列表 SQL 条数不随行数涨）；S6 新建 / 编辑 / 提交（N1 ~ N3：
商品推定写回与明细、``star_create``、``others_in_progress``、转移全表的编辑 / 提交两列、提交缺项、提交留痕、goods-members）。
角色一律用 ``flow_users``（迁移 seed 的真实角色）；造数用 ``negotiation_factory`` 直插任意状态。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from freezegun import freeze_time
from sqlalchemy import event, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.flow.exceptions import FlowActionForbiddenError, FlowGateMissingError
from app.modules.negotiation.exceptions import NegotiationNotFoundError
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.repository import NegotiationRepository
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationListFilters,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.schemas import GoodsItemIn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_STATES = ("草稿", "主管待审", "审核驳回", "待老板审核", "老板退回", "待补收货地址", "已定稿")
_OVERDUE_KEY = "主管待审超时"
_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13812345678",
    "receiver_address": "浙江省杭州市西湖区某路 1 号",
}
_T0 = datetime(2026, 10, 10, 2, 0, tzinfo=UTC)


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
                    "SELECT event_type, from_state, to_state, payload, actor_id, line_id"
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

        async def _lost(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(NegotiationRepository, "update_state", _lost)
        svc = NegotiationService(session)
        for call in (
            svc.update(draft.id, NegotiationUpdate(remark="y"), flow_users.pr),
            svc.submit(draft.id, flow_users.pr),
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

        # 主管通过、老板退回（S7 之前直接改库造这一步）
        stored = await session.get(Negotiation, created.id)
        assert stored is not None
        stored.status = "老板退回"
        stored.reviewed_by = flow_users.pr_manager.id
        stored.reviewed_at = _T0
        stored.review_opinion = "主管：可以"
        stored.final_reviewed_by = flow_users.admin.id
        stored.final_reviewed_at = _T0
        stored.final_review_opinion = "老板：价格再压"
        await session.flush()

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
        assert [(e[0], e[1], e[2]) for e in events] == [
            ("negotiation.created", None, "草稿"),
            ("negotiation.submitted", "草稿", "主管待审"),
            ("negotiation.submitted", "老板退回", "主管待审"),
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
        assert events[2][3] == {
            "snapshot": {**snapshot, "quote_amount": "300.00"},
            "diff": {"quote_amount": {"before": "500.00", "after": "300.00"}},
        }
        assert {e[4] for e in events} == {flow_users.pr.id}

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
