"""时间线读接口 ``GET /api/lines/{line_id}/events``（流程线设计 6.5；9.3 G 块「时间线三层过滤」）。

一条「PR 甲谈款定稿生成推广单」的线，谈款段 3 条（提交快照、定稿、定稿前留言）、推广段 7 条（建单、推送、收款码、
审核通过、推广阶段贴图留言、复盘、PATCH），按角色读：
- 第 1 层：仓库、线外 PR、不存在的线 → 404 ``LINE_NOT_FOUND``；财务没有 ``promotion:read``，靠结款单进来（注入 T_403、T_FIN）
- 第 2 层：财务只见谈款 / 留言 / 结款三组；PR 乙看 PR 甲的单 → 谈款段折成 ``hidden_segments`` 一行、只见推广阶段留言（注入 T_ROW）；
  个人撤销分组 scope 生效
- 第 3 层：运营删服务费 / 应付 / 收件（含 snapshot、diff 内层），收款码附件不签名（注入 T_ATT：先签后滤 → 签过收款码）；
  没登记的键连管理员也删
- 上限 500：501 条 → ``truncated``、只回最新 500 条（注入 T_TRUNC）；SQL 条数与事件条数无关，事件只查 1 次
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.flow.exceptions import LineNotFoundError
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.timeline import HIDDEN_NEGOTIATION_REASON, TIMELINE_LIMIT, LineEventService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_T = LineEventType


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """测试环境没配 R2：签名换成可断言的假地址，记下签过哪些 key。"""
    keys: list[str] = []

    def _fake(bucket: str, key: str, *, expires_in: int = 900) -> str:
        keys.append(key)
        return f"signed://{bucket}/{key}?ttl={expires_in}"

    monkeypatch.setattr(attachment_service, "get_signed_url", _fake)
    return keys


@dataclass
class _Line:
    line_id: UUID
    promotion_id: UUID
    negotiation_id: UUID
    qr_key: str
    image_key: str


def _all_keys(value: Any) -> set[str]:
    """payload 各层出现过的键（含 snapshot / diff 内层）。"""
    if isinstance(value, dict):
        out = set(value)
        for v in value.values():
            out |= _all_keys(v)
        return out
    if isinstance(value, list):
        return set().union(*(_all_keys(v) for v in value)) if value else set()
    return set()


async def _build_line(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    negotiation_factory: Any,
    attachment_factory: Any,
) -> _Line:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        quote_amount=Decimal("500.00"),
        receiver_name="张三",
        receiver_phone="13800000000",
        receiver_address="上海市某路 1 号",
        internal_code=f"TL{uuid4().hex[:10].upper()}",
    )
    neg = await negotiation_factory.negotiation(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        status="已定稿",
        promotion_id=promo.id,
        line_id=promo.line_id,
    )
    qr = await attachment_factory.attachment(
        purpose="payment_qr", r2_key=f"qr/{uuid4().hex}.png", mime_type="image/png"
    )
    image = await attachment_factory.attachment(
        purpose="line_comment_image", r2_key=f"cm/{uuid4().hex}.jpg"
    )
    repo = LineEventRepository(session)
    tid, line = promo.tenant_id, promo.line_id
    pr, mgr = flow_users.pr.id, flow_users.pr_manager.id

    async def add(event_type: LineEventType, actor: UUID | None, **kw: Any) -> None:
        await repo.append(tenant_id=tid, line_id=line, event_type=event_type, actor_id=actor, **kw)

    # 谈款段（negotiation_id 非空）
    await add(
        _T.NEGOTIATION_SUBMITTED,
        pr,
        negotiation_id=neg.id,
        from_state="草稿",
        to_state="主管待审",
        payload={
            "snapshot": {
                "platform": "小红书",
                "quote_amount": "500.00",
                "receiver_name": "张三",
                "secret": "x",
            }
        },
    )
    await add(_T.LINE_COMMENT, pr, negotiation_id=neg.id, note="谈款阶段留言")
    await add(
        _T.NEGOTIATION_FINALIZED,
        mgr,
        negotiation_id=neg.id,
        to_state="已定稿",
        payload={"promotion_id": str(promo.id)},
    )
    # 推广段（只挂 promotion_id）
    await add(
        _T.PROMOTION_CREATED,
        pr,
        promotion_id=promo.id,
        payload={"source": "谈款定稿", "negotiation_id": str(neg.id)},
    )
    await add(
        _T.SHIP_PUSHED,
        mgr,
        promotion_id=promo.id,
        from_state="待发货",
        to_state="待打单",
        payload={
            "items": [["短裙", "黑", "M"]],
            "diff": {
                "receiver_name": {"before": None, "after": "张三"},
                "ship_courier": {"before": None, "after": "顺丰"},
            },
        },
    )
    await add(_T.PROMOTION_PAYMENT_QR_UPLOADED, pr, promotion_id=promo.id, attachment_id=qr.id)
    await add(
        _T.REVIEW_APPROVED,
        mgr,
        promotion_id=promo.id,
        from_state="待核查",
        to_state="待付款",
        payload={"payable": "500.00", "destination": "待付款"},
    )
    await add(
        _T.LINE_COMMENT, pr, promotion_id=promo.id, note="推广阶段留言", attachment_id=image.id
    )
    await add(_T.RETRO_WRITTEN, pr, promotion_id=promo.id, payload={"retrospective_id": "r1"})
    await add(
        _T.PROMOTION_UPDATED,
        pr,
        promotion_id=promo.id,
        payload={
            "diff": {
                "remark": {"before": None, "after": "补充"},
                "quote_amount": {"before": "500.00", "after": "600.00"},
            },
            "secret": 1,
        },
    )
    return _Line(
        line_id=line,
        promotion_id=promo.id,
        negotiation_id=neg.id,
        qr_key=qr.r2_key,
        image_key=image.r2_key,
    )


@pytest.fixture
async def line(
    tenant_ctx: None,
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    negotiation_factory: Any,
    attachment_factory: Any,
) -> _Line:
    return await _build_line(
        session,
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        negotiation_factory,
        attachment_factory,
    )


def _by_type(resp: Any) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for e in resp.events:
        out.setdefault(e.event_type, []).append(e)
    return out


async def _revoke(session: AsyncSession, tenant: Any, user: Any, scope: str) -> None:
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=perm.id, effect="revoke"
        )
    )
    await session.flush()


async def _call(session: AsyncSession, user: Any, path: str) -> Any:
    """真实路由 + 用户的有效权限（照 test_line_events._call）。"""
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
            return await c.get(path)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.mark.usefixtures("tenant_ctx")
class TestLayerOne:
    async def test_warehouse_and_outsider_get_404(
        self,
        session: AsyncSession,
        flow_users: Any,
        line: _Line,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
        signed: list[str],
    ) -> None:
        svc = LineEventService(session)
        # 场景有效：PR 甲读得到
        assert (await svc.read(line.line_id, flow_users.pr)).events
        with pytest.raises(LineNotFoundError) as exc_info:
            await svc.read(line.line_id, flow_users.warehouse)
        assert (exc_info.value.code, exc_info.value.status_code) == ("LINE_NOT_FOUND", 404)
        with pytest.raises(LineNotFoundError):
            await svc.read(uuid4(), flow_users.admin)

        # PR 甲的草稿谈款：PR 乙（线外）404，PR 甲自己读得到
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        draft = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr
        )
        await LineEventRepository(session).append(
            tenant_id=draft.tenant_id,
            line_id=draft.line_id,
            event_type=_T.NEGOTIATION_CREATED,
            actor_id=flow_users.pr.id,
            negotiation_id=draft.id,
        )
        own = await svc.read(draft.line_id, flow_users.pr)
        assert [e.event_type for e in own.events] == ["negotiation.created"]
        assert own.promotion is None and own.negotiation is not None
        with pytest.raises(LineNotFoundError):
            await svc.read(draft.line_id, flow_users.pr2)

    async def test_route_404_and_200(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        """路由只要求登录：仓库进得了路由、在 service 得 404；PR 200。"""
        resp = await _call(session, flow_users.warehouse, f"/api/lines/{line.line_id}/events")
        assert (resp.status_code, resp.json()["code"]) == (404, "LINE_NOT_FOUND"), resp.text
        ok = await _call(session, flow_users.pr, f"/api/lines/{line.line_id}/events")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["line_id"] == str(line.line_id)
        assert set(body) == {
            "line_id",
            "negotiation",
            "promotion",
            "next_step",
            "events",
            "hidden_segments",
            "legacy",
            "truncated",
            "ui",
        }

    async def test_finance_enters_through_settlement(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        signed: list[str],
    ) -> None:
        """没有谈款的单（导入 / 补录）：财务没有 promotion:read，有结款单才进得来。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=style, blogger=blogger, pr=flow_users.pr, publish_status="已发布"
        )
        await LineEventRepository(session).append(
            tenant_id=promo.tenant_id,
            line_id=promo.line_id,
            event_type=_T.REVIEW_APPROVED,
            actor_id=flow_users.pr_manager.id,
            promotion_id=promo.id,
            payload={"payable": "500.00"},
        )
        svc = LineEventService(session)
        with pytest.raises(LineNotFoundError):
            await svc.read(promo.line_id, flow_users.finance)
        await settlement_factory.settlement(
            style=style, blogger=blogger, promotion=promo, pr=flow_users.pr
        )
        resp = await svc.read(promo.line_id, flow_users.finance)
        assert resp.promotion is not None and resp.promotion.id == promo.id
        assert resp.negotiation is None
        assert [(e.event_type, e.payload) for e in resp.events] == [
            ("review.approved", {"payable": "500.00"})
        ]


@pytest.mark.usefixtures("tenant_ctx")
class TestLayerTwo:
    async def test_admin_sees_everything_newest_first(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        resp = await LineEventService(session).read(line.line_id, flow_users.admin)
        assert len(resp.events) == 10
        seqs = [e.seq for e in resp.events]
        assert seqs == sorted(seqs, reverse=True)
        assert resp.events[0].event_type == "promotion.updated"
        assert {e.group for e in resp.events} == {"谈款", "留言", "推广", "发货", "结款", "复盘"}
        assert resp.hidden_segments == [] and resp.truncated is False
        assert resp.negotiation is not None and resp.negotiation.status == "已定稿"
        assert resp.promotion is not None and resp.promotion.id == line.promotion_id
        pushed = _by_type(resp)["ship.pushed"][0]
        mgr = flow_users.pr_manager
        assert pushed.actor is not None
        assert (pushed.actor.id, pushed.actor.display_name) == (
            mgr.id,
            mgr.display_name or mgr.username,
        )
        assert (pushed.from_state, pushed.to_state) == ("待发货", "待打单")
        # 附件签名 900 秒
        qr = _by_type(resp)["promotion.payment_qr_uploaded"][0].attachment
        assert qr is not None and qr.url == f"signed://private/{line.qr_key}?ttl=900"
        assert qr.mime == "image/png"

    async def test_pr2_sees_hidden_negotiation_segment(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        """7c-10：PR 乙打开 PR 甲谈款生成的推广单——推广段照看，谈款段（含定稿前的留言）折成一行。"""
        svc = LineEventService(session)
        resp = await svc.read(line.line_id, flow_users.pr2)
        assert resp.negotiation is None and resp.promotion is not None
        assert [h.model_dump() for h in resp.hidden_segments] == [
            {"group": "谈款", "count": 3, "reason": HIDDEN_NEGOTIATION_REASON}
        ]
        types = _by_type(resp)
        assert len(resp.events) == 7
        assert not any(t.startswith("negotiation.") for t in types)
        assert [e.note for e in types["line.comment"]] == ["推广阶段留言"]

        # 对照：PR 甲（谈款页按 line_id 读）两段都看得到，定稿前后的留言都在
        own = await svc.read(line.line_id, flow_users.pr)
        assert own.hidden_segments == [] and len(own.events) == 10
        assert [e.note for e in _by_type(own)["line.comment"]] == ["推广阶段留言", "谈款阶段留言"]

    async def test_finance_only_three_groups(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        """财务（靠 negotiation_all:read 看得到已定稿谈款）：只回谈款、留言、结款三组。"""
        resp = await LineEventService(session).read(line.line_id, flow_users.finance)
        assert {e.group for e in resp.events} == {"谈款", "留言", "结款"}
        assert sorted(e.event_type for e in resp.events) == sorted(
            [
                "negotiation.submitted",
                "line.comment",
                "negotiation.finalized",
                "promotion.payment_qr_uploaded",
                "review.approved",
                "line.comment",
            ]
        )
        assert resp.hidden_segments == []
        # 财务看服务费 / 应付 / 收款码，看不到收件
        types = _by_type(resp)
        assert types["review.approved"][0].payload == {"payable": "500.00", "destination": "待付款"}
        assert types["negotiation.submitted"][0].payload == {
            "snapshot": {"platform": "小红书", "quote_amount": "500.00"}
        }
        qr = types["promotion.payment_qr_uploaded"][0].attachment
        assert qr is not None and qr.url is not None

    async def test_personal_revoke_of_group(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        line: _Line,
        signed: list[str],
    ) -> None:
        svc = LineEventService(session)
        before = await svc.read(line.line_id, flow_users.pr)
        assert "发货" in {e.group for e in before.events}
        await _revoke(session, tenant_a, flow_users.pr, "field.line_event.shipping:read")
        after = await svc.read(line.line_id, flow_users.pr)
        assert "发货" not in {e.group for e in after.events}
        assert len(after.events) == len(before.events) - 1
        # 分组级看不到的不进 hidden_segments
        assert after.hidden_segments == []

    async def test_unknown_event_type_is_skipped(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        """库里没有 CHECK：以后加了类型没登记分组，读接口不出、不报错。"""
        await session.execute(
            sa_text(
                "INSERT INTO line_event (id, tenant_id, line_id, promotion_id, event_type, payload, "
                "created_at, updated_at) VALUES (:id, :t, :l, :p, 'mystery.done', '{}'::jsonb, "
                "NOW(), NOW())"
            ),
            {
                "id": uuid4(),
                "t": flow_users.tenant.id,
                "l": line.line_id,
                "p": line.promotion_id,
            },
        )
        resp = await LineEventService(session).read(line.line_id, flow_users.admin)
        assert len(resp.events) == 10
        assert "mystery.done" not in {e.event_type for e in resp.events}


@pytest.mark.usefixtures("tenant_ctx")
class TestLayerThree:
    async def test_operations_loses_amount_receiver_and_qr(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        resp = await LineEventService(session).read(line.line_id, flow_users.operations)
        # 场景有效：运营看得到这几类事件
        types = _by_type(resp)
        for t in ("review.approved", "ship.pushed", "negotiation.submitted", "promotion.updated"):
            assert t in types, t
        keys = set().union(*(_all_keys(e.payload) for e in resp.events))
        assert keys.isdisjoint(
            {"quote_amount", "payable", "receiver_name", "receiver_phone", "receiver_address"}
        ), keys
        assert types["review.approved"][0].payload == {"destination": "待付款"}
        # diff 的条目按字段名同样过滤
        assert types["ship.pushed"][0].payload == {
            "items": [["短裙", "黑", "M"]],
            "diff": {"ship_courier": {"before": None, "after": "顺丰"}},
        }
        assert types["promotion.updated"][0].payload == {
            "diff": {"remark": {"before": None, "after": "补充"}}
        }
        # 收款码附件：不出、也没签过名；贴图留言的附件照签
        assert types["promotion.payment_qr_uploaded"][0].attachment is None
        assert line.qr_key not in signed
        image = types["line.comment"][0].attachment
        assert image is not None and image.url == f"signed://private/{line.image_key}?ttl=900"

    async def test_unregistered_keys_dropped_even_for_admin(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        resp = await LineEventService(session).read(line.line_id, flow_users.admin)
        types = _by_type(resp)
        assert types["promotion.updated"][0].payload == {
            "diff": {
                "remark": {"before": None, "after": "补充"},
                "quote_amount": {"before": "500.00", "after": "600.00"},
            }
        }
        assert types["negotiation.submitted"][0].payload == {
            "snapshot": {"platform": "小红书", "quote_amount": "500.00", "receiver_name": "张三"}
        }


@pytest.mark.usefixtures("tenant_ctx")
class TestUiComment:
    async def test_comment_action_by_role(
        self, session: AsyncSession, flow_users: Any, line: _Line, signed: list[str]
    ) -> None:
        svc = LineEventService(session)
        for user in (flow_users.pr, flow_users.pr2, flow_users.pr_manager):
            resp = await svc.read(line.line_id, user)
            assert resp.ui["actions"]["comment"] == {"state": "enabled"}
        for user in (flow_users.operations, flow_users.finance):
            resp = await svc.read(line.line_id, user)
            assert "comment" not in resp.ui["actions"]

    async def test_negotiation_only_line_uses_negotiation_matrix(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
        signed: list[str],
    ) -> None:
        """还没定稿：按谈款矩阵——主管待审时 PR 可留言、运营只读（disabled + 原因）。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        neg = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="主管待审"
        )
        svc = LineEventService(session)
        pr_view = await svc.read(neg.line_id, flow_users.pr)
        assert pr_view.ui["actions"]["comment"] == {"state": "enabled"}
        assert pr_view.negotiation is not None and pr_view.negotiation.status == "主管待审"
        ops_view = await svc.read(neg.line_id, flow_users.operations)
        assert ops_view.ui["actions"]["comment"]["state"] == "disabled"


@pytest.mark.usefixtures("tenant_ctx")
class TestLimitAndQueries:
    async def test_truncated_after_500(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        signed: list[str],
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)

        async def bulk(n: int) -> None:
            await session.execute(
                sa_text(
                    "INSERT INTO line_event (id, tenant_id, line_id, promotion_id, event_type, "
                    "payload, created_at, updated_at) "
                    "SELECT gen_random_uuid(), :t, :l, :p, 'promotion.updated', "
                    "jsonb_build_object(CAST('remark' AS text), g), NOW(), NOW() "
                    "FROM generate_series(1, CAST(:n AS int)) AS g"
                ),
                {"t": promo.tenant_id, "l": promo.line_id, "p": promo.id, "n": n},
            )

        svc = LineEventService(session)
        await bulk(TIMELINE_LIMIT)
        full = await svc.read(promo.line_id, flow_users.pr)
        assert (len(full.events), full.truncated) == (TIMELINE_LIMIT, False)
        await bulk(1)
        over = await svc.read(promo.line_id, flow_users.pr)
        assert (len(over.events), over.truncated) == (TIMELINE_LIMIT, True)
        # 留下的是最新的 500 条：最老的一条被截掉
        assert over.events[0].seq > full.events[0].seq
        assert over.events[-1].seq == full.events[-2].seq

    async def test_query_count_independent_of_event_count(
        self,
        session: AsyncSession,
        engine: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
        signed: list[str],
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        image = await attachment_factory.attachment(purpose="line_comment_image")
        repo = LineEventRepository(session)
        users = (flow_users.pr, flow_users.pr_manager, flow_users.admin)

        async def add(i: int) -> None:
            await repo.append(
                tenant_id=promo.tenant_id,
                line_id=promo.line_id,
                event_type=_T.LINE_COMMENT if i % 2 else _T.PROMOTION_UPDATED,
                actor_id=users[i % 3].id,
                promotion_id=promo.id,
                attachment_id=image.id if i % 2 else None,
                note=f"第 {i} 条",
            )

        svc = LineEventService(session)

        async def count() -> tuple[int, int, int]:
            statements: list[str] = []

            def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
                statements.append(statement)

            event.listen(engine.sync_engine, "before_cursor_execute", _capture)
            try:
                resp = await svc.read(promo.line_id, flow_users.pr)
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", _capture)
            on_events = sum("FROM line_event" in s for s in statements)
            return len(statements), on_events, len(resp.events)

        for i in range(2):
            await add(i)
        await svc.read(promo.line_id, flow_users.pr)  # 预热（阈值配置等首次读取）
        two = await count()
        for i in range(2, 50):
            await add(i)
        fifty = await count()
        assert (two[2], fifty[2]) == (2, 50)
        assert two[0] == fifty[0], (two, fifty)
        assert two[1] == fifty[1] == 1
