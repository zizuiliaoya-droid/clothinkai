"""主线事件落库（流程线设计 6.2 ~ 6.4；9.3 G 块「只追加、同事务」）。

- ``LineEventRepository.append`` 只 flush 不提交：业务回滚，事件一起没（注入 G6 在 append 里 commit → 回滚用例红）
- ``list_by_line`` 按 ``seq`` 排、只取这条线
- payload 上限 8 KB，与 ``octet_length(payload::text)`` 同一把尺子；超了 422，不落库
- ``create_promotion`` 同事务写 ``promotion.created``：直接新建 line_id = 库发号、来源「直接新建」；
  谈款定稿继承谈款的号、来源「谈款定稿」带谈款 id，动作人是确认的人（注入 S3_SRC 来源写死 → 红）

断言落库值时直接 SELECT；rollback 之后 ORM 对象全部过期，要比的 id 先取出来。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission
from app.modules.flow.exceptions import LineEventPayloadTooLargeError
from app.modules.flow.line_events import (
    EVENT_GROUP_KEYS,
    PAYLOAD_MAX_BYTES,
    LineEventRepository,
    LineEventType,
    payload_size,
)
from app.modules.promotion.enums import CooperationMode, ShipStatus
from app.modules.promotion.schemas import PromotionCreate
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _events_of(session: AsyncSession, *, promotion_id: UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                sa_text(
                    "SELECT tenant_id, line_id, negotiation_id, promotion_id, event_type, "
                    "from_state, to_state, note, attachment_id, payload, actor_id "
                    "FROM line_event WHERE promotion_id = :pid ORDER BY seq"
                ),
                {"pid": promotion_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _promotion_line(session: AsyncSession, promotion_id: UUID) -> UUID:
    return (
        await session.execute(
            sa_text("SELECT line_id FROM promotion WHERE id = :pid"), {"pid": promotion_id}
        )
    ).scalar_one()


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限（照 test_promotion_shipping._call）。"""
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


@pytest.mark.usefixtures("tenant_ctx")
class TestRepository:
    async def test_append_rolls_back_with_business(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """同事务（6.4）：append 之后业务回滚 → 事件表没有新行。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        pid, tid, line_id = promo.id, promo.tenant_id, promo.line_id
        await session.commit()

        event = await LineEventRepository(session).append(
            tenant_id=tid,
            line_id=line_id,
            event_type=LineEventType.PROMOTION_UPDATED,
            actor_id=flow_users.pr.id,
            promotion_id=pid,
        )
        assert event.seq is not None and event.payload == {}
        # 场景有效性：回滚前确实写进去了
        assert len(await _events_of(session, promotion_id=pid)) == 1
        await session.rollback()
        assert await _events_of(session, promotion_id=pid) == []

    async def test_list_by_line_orders_by_seq_and_scopes_line(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        # 一条用例建两张：编号给足 8 位随机（工厂默认 3 位，照 test_promotion_shipping）
        promo, other = [
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=flow_users.pr,
                internal_code=f"LEV{uuid4().hex[:8].upper()}",
            )
            for _ in range(2)
        ]
        repo = LineEventRepository(session)
        order = [
            LineEventType.SHIP_INCLUDED,
            LineEventType.PROMOTION_CREATED,
            LineEventType.SHIP_PUSHED,
        ]
        for i, t in enumerate(order):
            await repo.append(
                tenant_id=promo.tenant_id,
                line_id=promo.line_id,
                event_type=t,
                actor_id=None if i == 1 else flow_users.pr_manager.id,
                promotion_id=promo.id,
                from_state="甲" if i == 0 else None,
                to_state="乙" if i == 0 else None,
                note="备注" if i == 2 else None,
                payload={"i": i, "system": "test"} if i == 1 else {"i": i},
            )
        await repo.append(
            tenant_id=other.tenant_id,
            line_id=other.line_id,
            event_type=LineEventType.PROMOTION_CREATED,
            actor_id=flow_users.pr.id,
            promotion_id=other.id,
        )

        got = await repo.list_by_line(tenant_id=promo.tenant_id, line_id=promo.line_id)
        assert [e.event_type for e in got] == [t.value for t in order]
        seqs = [e.seq for e in got]
        assert seqs == sorted(seqs) and len(set(seqs)) == 3
        assert [e.payload["i"] for e in got] == [0, 1, 2]
        assert (got[0].from_state, got[0].to_state, got[2].note) == ("甲", "乙", "备注")
        assert got[1].actor_id is None and got[0].actor_id == flow_users.pr_manager.id
        assert {e.promotion_id for e in got} == {promo.id}
        # 别的租户读不到（tenant_id 显式过滤，不依赖上下文）
        assert await repo.list_by_line(tenant_id=uuid4(), line_id=promo.line_id) == []

    async def test_payload_limit_same_ruler_as_octet_length(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """恰好 8192 字节能写、库里 ``octet_length(payload::text)`` 也是 8192；多 1 字节 → 422 不落库。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        repo = LineEventRepository(session)
        base = payload_size({"diff": ""})
        # 中文 3 字节一个：凑满 8192（余数用 ASCII 补）
        n_cn, n_ascii = divmod(PAYLOAD_MAX_BYTES - base, 3)
        exact = {"diff": "改" * n_cn + "a" * n_ascii}
        assert payload_size(exact) == PAYLOAD_MAX_BYTES
        event = await repo.append(
            tenant_id=promo.tenant_id,
            line_id=promo.line_id,
            event_type=LineEventType.PROMOTION_UPDATED,
            actor_id=flow_users.pr.id,
            promotion_id=promo.id,
            payload=exact,
        )
        octets = (
            await session.execute(
                sa_text("SELECT octet_length(payload::text) FROM line_event WHERE id = :id"),
                {"id": event.id},
            )
        ).scalar_one()
        assert octets == PAYLOAD_MAX_BYTES

        with pytest.raises(LineEventPayloadTooLargeError) as exc_info:
            await repo.append(
                tenant_id=promo.tenant_id,
                line_id=promo.line_id,
                event_type=LineEventType.PROMOTION_UPDATED,
                actor_id=flow_users.pr.id,
                promotion_id=promo.id,
                payload={"diff": exact["diff"] + "a"},
            )
        assert (exc_info.value.code, exc_info.value.status_code) == (
            "LINE_EVENT_PAYLOAD_TOO_LARGE",
            422,
        )
        assert exc_info.value.details["size"] == PAYLOAD_MAX_BYTES + 1
        assert len(await _events_of(session, promotion_id=promo.id)) == 1

    async def test_group_keys_match_seeded_field_scopes(self, session: AsyncSession) -> None:
        """每个分组的个人授予 / 撤销 scope ``field.line_event.<键>:read`` 都已随迁移入册。"""
        scopes = set(
            (
                await session.execute(
                    select(Permission.scope).where(Permission.scope.like("field.line_event.%"))
                )
            )
            .scalars()
            .all()
        )
        assert scopes == {f"field.line_event.{k}:read" for k in EVENT_GROUP_KEYS}


@pytest.mark.usefixtures("tenant_ctx")
class TestPromotionCreated:
    async def test_post_route_writes_one_event_with_db_line_id(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """主管直接新建（7c-9）：line_id 由库发号并回读到响应；同事务 1 条 promotion.created、来源「直接新建」。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        body = {
            "style_id": str(style.id),
            "blogger_id": str(blogger.id),
            "platform": "小红书",
            "cooperation_mode": "送拍",
        }
        resp = await _call(session, flow_users.pr_manager, "POST", "/api/promotions/", body)
        assert resp.status_code == 201, resp.text
        pid = UUID(resp.json()["id"])
        db_line = await _promotion_line(session, pid)
        assert resp.json()["line_id"] == str(db_line)

        events = await _events_of(session, promotion_id=pid)
        assert events == [
            {
                "tenant_id": flow_users.tenant.id,
                "line_id": db_line,
                "negotiation_id": None,
                "promotion_id": pid,
                "event_type": "promotion.created",
                "from_state": None,
                "to_state": None,
                "note": None,
                "attachment_id": None,
                "payload": {"source": "直接新建"},
                "actor_id": flow_users.pr_manager.id,
            }
        ]

    async def test_negotiation_finalize_inherits_line_and_records_source(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
    ) -> None:
        """谈款定稿（N9 的建单半步）：推广单继承谈款的号；事件只挂推广单、来源「谈款定稿」带谈款 id，动作人是确认的人。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        neg = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="待补收货地址"
        )
        resp = await PromotionService(session).create_promotion(
            PromotionCreate(
                style_id=style.id,
                blogger_id=blogger.id,
                platform="小红书",
                cooperation_mode=CooperationMode.GIFT,
            ),
            flow_users.pr,
            autocommit=False,
            ship_status=ShipStatus.PENDING,
            line_id=neg.line_id,
            negotiation_id=neg.id,
            actor_id=flow_users.pr_manager.id,
        )
        assert resp.line_id == neg.line_id
        assert await _promotion_line(session, resp.id) == neg.line_id
        events = await _events_of(session, promotion_id=resp.id)
        assert [
            (e["event_type"], e["line_id"], e["negotiation_id"], e["payload"], e["actor_id"])
            for e in events
        ] == [
            (
                "promotion.created",
                neg.line_id,
                None,
                {"source": "谈款定稿", "negotiation_id": str(neg.id)},
                flow_users.pr_manager.id,
            )
        ]
        # 推广单的建单人仍是对接 PR
        assert resp.pr_id == flow_users.pr.id

    async def test_create_rolled_back_leaves_no_event(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """调用方（谈款定稿）在建单之后失败回滚 → 推广单与 promotion.created 都不落库。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        await session.commit()
        resp = await PromotionService(session).create_promotion(
            PromotionCreate(
                style_id=style.id,
                blogger_id=blogger.id,
                platform="小红书",
                cooperation_mode=CooperationMode.GIFT,
            ),
            flow_users.pr_manager,
            autocommit=False,
        )
        pid = resp.id
        assert len(await _events_of(session, promotion_id=pid)) == 1
        await session.rollback()
        assert await _events_of(session, promotion_id=pid) == []
        left = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        assert left == 0
