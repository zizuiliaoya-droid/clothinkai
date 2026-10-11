"""流程线 PR-3：谈款两级审核（设计 3.2、7.2、9.3 B 块）。

本文件按条目逐步长：S5 可见范围与读接口（列表 / 计数 / 详情的可见范围、超 36 小时、``ui``、收件投影、
``receiver_suggestion``、商品明细与品名、列表 SQL 条数不随行数涨）。
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

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.negotiation.exceptions import NegotiationNotFoundError
from app.modules.negotiation.schemas import NegotiationListFilters
from app.modules.negotiation.service import NegotiationService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

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
