"""给现有推广单动作补写的主线事件（流程线设计 6.3、6.4；PR-4 S7 起逐条加）。

守的不变量：
- 每个动作至少一条「写了事件」断言：类型、挂哪边、from / to、note、附件、payload（键都已登记，``assert_payload_registered``）
- 同事务（6.4，G6）：动作在写事件之后失败 → 事件跟着回滚（注入 G6：append 里 commit）
- 事件写失败就让动作失败（6.4、11-49）：发布顺手关催发任务那段吞异常，``urge.closed`` 不能挪进去（注入 E_SWALLOW）
- 系统写的事件 ``actor_id = NULL``、payload 带 ``system``（自动扫描 = ``urge_scan``）；自动催发不逐天记（11-22）

S7：发布、取消（待打单多一条 ``ship.withdrawn``）、催发（手动催 / 批量催 / 手动关闭 / 扫描建任务与收口）。
S8：旧召回（发起 / 成功 / 失败）、旧推广审核（通过 / 驳回）与重提、录 7 天数据、旧复盘（写 / 确认 / 打回）。
S9：收款码上传 / 绑定、品牌词截图、寄回单号；PATCH 拆成 ``promotion.updated`` / ``ship.receiver_changed`` /
``ship.items_changed``（金额只列字段名、收件单独一条、明细值不放编码），PUT items；无变化不写。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.timeline import LineEventService
from app.modules.promotion.enums import RejectReasonCategory, ReviewAction
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionCancelRequest,
    PromotionPaymentQrBindRequest,
    PromotionPublishRequest,
    PromotionRecallStartRequest,
    PromotionRecallSuccessRequest,
    PromotionResubmitRequest,
    PromotionReturnWaybillRequest,
    PromotionReviewRequest,
    PromotionUpdate,
    RetrospectiveWriteRequest,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.service import UrgeService
from tests.flow_event_helpers import assert_payload_registered, events_of
from tests.integration.test_promotion_shipping import _call, _goods

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_T = LineEventType
# 最小合法 PNG（check_image_payload 只验魔数）
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PUBLISH = PromotionPublishRequest(
    publish_url="https://www.xiaohongshu.com/explore/abc", actual_publish_date=get_today()
)


class _FakeS3:
    """只替掉联网的调用（照 test_line_comments）。"""

    def put_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def delete_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _promo(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any, **kw: Any
) -> Any:
    style = kw.pop("style", None) or await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(
        style=style, blogger=blogger, pr=flow_users.pr, brand_comment=True, **kw
    )


def _types(events: list[Any]) -> list[str]:
    return [e.event_type for e in events]


def _check_registered(events: list[Any]) -> None:
    for e in events:
        assert_payload_registered(e.payload)


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestPublish:
    async def test_publish_writes_published_and_urge_closed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid, brand_att = promo.line_id, promo.id, promo.brand_comment_attachment_id
        task = await UrgeService(session).urge_once(pid, flow_users.pr)
        before = len(await events_of(session, line_id=line_id))

        await PromotionService(session).publish(pid, _PUBLISH, flow_users.pr)

        new = (await events_of(session, line_id=line_id))[before:]
        assert _types(new) == [_T.PROMOTION_PUBLISHED.value, _T.URGE_CLOSED.value]
        published, closed = new
        assert (published.from_state, published.to_state) == ("未发布", "已发布")
        assert published.promotion_id == pid and published.negotiation_id is None
        assert published.actor_id == flow_users.pr.id
        assert brand_att is not None and published.attachment_id == brand_att
        assert published.payload == {
            "publish_url": _PUBLISH.publish_url,
            "actual_publish_date": _PUBLISH.actual_publish_date.isoformat(),
        }
        assert closed.actor_id == flow_users.pr.id and closed.promotion_id == pid
        assert closed.payload == {"urge_task_id": str(task.id), "close_reason": "博主已发布"}
        _check_registered(new)

    async def test_publish_without_urge_task_writes_no_close(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id = promo.line_id
        await PromotionService(session).publish(promo.id, _PUBLISH, flow_users.pr)
        assert _types(await events_of(session, line_id=line_id)) == [_T.PROMOTION_PUBLISHED.value]

    async def test_publish_failure_after_event_rolls_event_back(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """G 块「同事务回滚」：事件写完之后审计抛错 → 发布失败，事件 0 行（注入 G6：append 里 commit）。"""
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        await session.commit()
        seen: list[int] = []
        original = AuditService.log

        async def _boom(self: AuditService, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("action") == "promotion.publish":
                # 场景有效性：抛错之前事件确实已经写进去了
                seen.append(
                    len(
                        await events_of(session, line_id=line_id, event_type=_T.PROMOTION_PUBLISHED)
                    )
                )
                raise RuntimeError("audit down")
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(AuditService, "log", _boom)
        with pytest.raises(RuntimeError, match="audit down"):
            await PromotionService(session).publish(pid, _PUBLISH, flow_users.pr)
        assert seen == [1]
        await session.rollback()
        assert await events_of(session, line_id=line_id) == []
        status = (
            await session.execute(
                sa_text("SELECT publish_status FROM promotion WHERE id = :p"), {"p": pid}
            )
        ).scalar_one()
        assert status == "未发布"

    async def test_urge_closed_write_failure_blocks_publish(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """关催发任务吞异常，但 ``urge.closed`` 写不进去不能吞：发布跟着失败（注入 E_SWALLOW）。"""
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        await UrgeService(session).urge_once(promo.id, flow_users.pr)
        original = LineEventRepository.append

        async def _append(self: LineEventRepository, **kwargs: Any) -> Any:
            if kwargs["event_type"] is _T.URGE_CLOSED:
                raise RuntimeError("line_event down")
            return await original(self, **kwargs)

        monkeypatch.setattr(LineEventRepository, "append", _append)
        with pytest.raises(RuntimeError, match="line_event down"):
            await PromotionService(session).publish(promo.id, _PUBLISH, flow_users.pr)


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestCancel:
    async def test_cancel_printing_writes_cancelled_withdrawn_and_closed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status="待打单"
        )
        line_id, pid = promo.line_id, promo.id
        task = await UrgeService(session).urge_once(pid, flow_users.pr)
        before = len(await events_of(session, line_id=line_id))

        await PromotionService(session).cancel(
            pid, PromotionCancelRequest(cancel_reason="博主档期冲突"), flow_users.pr
        )

        new = (await events_of(session, line_id=line_id))[before:]
        assert _types(new) == [
            _T.PROMOTION_CANCELLED.value,
            _T.SHIP_WITHDRAWN.value,
            _T.URGE_CLOSED.value,
        ]
        cancelled, withdrawn, closed = new
        assert (cancelled.from_state, cancelled.to_state) == ("未发布", "已取消")
        assert cancelled.note == "博主档期冲突" and cancelled.actor_id == flow_users.pr.id
        assert (withdrawn.from_state, withdrawn.to_state) == ("待打单", "待发货")
        assert withdrawn.note == "推广已取消，已从仓库队列撤回"
        assert withdrawn.payload == {"by_cancel": True}
        assert closed.payload == {"urge_task_id": str(task.id), "close_reason": "已取消"}
        assert {e.promotion_id for e in new} == {pid}
        _check_registered(new)

    @pytest.mark.parametrize("ship_status", [None, "待发货", "已发货"])
    async def test_cancel_not_pushed_writes_no_withdrawn(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        ship_status: str | None,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            ship_status=ship_status,
        )
        line_id = promo.line_id
        await PromotionService(session).cancel(
            promo.id, PromotionCancelRequest(cancel_reason="不合作"), flow_users.pr
        )
        assert _types(await events_of(session, line_id=line_id)) == [_T.PROMOTION_CANCELLED.value]


# ---------------------------------------------------------------------------
# 催发
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestUrge:
    async def test_urge_once_opens_task_then_sends(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core import attachment as att_mod

        monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        svc = UrgeService(session)

        first = await svc.urge_once(
            pid, flow_users.pr, note="微信催了一次", screenshot=("a.png", "image/png", _PNG)
        )
        second = await svc.urge_once(pid, flow_users.pr, note="第二次")

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [
            _T.URGE_TASK_OPENED.value,
            _T.URGE_SENT.value,
            _T.URGE_SENT.value,
        ]
        opened, sent1, sent2 = events
        assert opened.payload == {"trigger": "手动", "urge_task_id": str(first.id)}
        assert opened.actor_id == flow_users.pr.id and opened.promotion_id == pid
        records = {r.note: r for r in second.records}
        assert sent1.note == "微信催了一次" and sent1.attachment_id is not None
        assert sent1.payload == {
            "trigger": "手动",
            "urge_task_id": str(first.id),
            "urge_record_id": str(records["微信催了一次"].id),
            "urge_count": 1,
        }
        assert sent2.note == "第二次" and sent2.attachment_id is None
        assert sent2.payload["urge_count"] == 2
        assert sent2.payload["urge_record_id"] == str(records["第二次"].id)
        shot = (
            await session.execute(
                sa_text("SELECT screenshot_attachment_id FROM urge_record WHERE id = :r"),
                {"r": records["微信催了一次"].id},
            )
        ).scalar_one()
        assert shot == sent1.attachment_id
        _check_registered(events)

    async def test_urge_batch_writes_per_line(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        style = await product_factory.style()
        a = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, style=style
        )
        b = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, style=style
        )
        svc = UrgeService(session)
        # b 先有任务：批量里只多一条 sent，不再 task_opened
        await svc.urge_once(b.id, flow_users.pr)
        b_before = len(await events_of(session, line_id=b.line_id))

        resp = await svc.urge_batch(style.id, flow_users.pr, note="批量催")

        assert resp.urged_count == 2
        a_events = await events_of(session, line_id=a.line_id)
        b_new = (await events_of(session, line_id=b.line_id))[b_before:]
        assert _types(a_events) == [_T.URGE_TASK_OPENED.value, _T.URGE_SENT.value]
        assert _types(b_new) == [_T.URGE_SENT.value]
        for sent, promo in ((a_events[1], a), (b_new[0], b)):
            assert sent.promotion_id == promo.id and sent.note == "批量催"
            record_id = (
                await session.execute(
                    sa_text(
                        "SELECT id FROM urge_record WHERE promotion_id = :p AND note = '批量催'"
                    ),
                    {"p": promo.id},
                )
            ).scalar_one()
            assert sent.payload["urge_record_id"] == str(record_id)
            assert sent.payload["trigger"] == "手动"
        assert b_new[0].payload["urge_count"] == 2
        _check_registered(a_events + b_new)

    async def test_close_task_writes_closed_with_reason(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id = promo.line_id
        svc = UrgeService(session)
        task = await svc.urge_once(promo.id, flow_users.pr)
        await svc.close_task(task.id, flow_users.pr_manager, reason="改走召回")
        closed = await events_of(session, line_id=line_id, event_type=_T.URGE_CLOSED)
        assert len(closed) == 1
        assert closed[0].note == "改走召回" and closed[0].actor_id == flow_users.pr_manager.id
        assert closed[0].promotion_id == promo.id
        assert closed[0].payload == {"urge_task_id": str(task.id), "close_reason": "手动关闭"}
        _check_registered(closed)

    async def test_scan_opens_and_closes_as_system(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """扫描写的事件 actor NULL、payload 带 system；自动催发本身不进时间线（11-22）。"""
        today = get_today()
        due = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            scheduled_publish_date=today + timedelta(days=2),
        )
        stale = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        svc = UrgeService(session)
        stale_task = await svc.urge_once(stale.id, flow_users.pr)
        await session.execute(
            sa_text("UPDATE promotion SET publish_status = '已发布' WHERE id = :p"),
            {"p": stale.id},
        )
        stale_before = len(await events_of(session, line_id=stale.line_id))

        result = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
        # 再扫一次：当天不重复计次，也不重复写事件
        await svc.scan_tenant(tenant_id=tenant_a.id, today=today)

        assert result.tasks_created == 1 and result.records_created == 1
        assert result.tasks_closed == 1
        due_events = await events_of(session, line_id=due.line_id)
        assert _types(due_events) == [_T.URGE_TASK_OPENED.value]
        task_id = (
            await session.execute(
                sa_text("SELECT id FROM urge_task WHERE promotion_id = :p"), {"p": due.id}
            )
        ).scalar_one()
        assert due_events[0].actor_id is None and due_events[0].promotion_id == due.id
        assert due_events[0].payload == {
            "trigger": "自动",
            "urge_task_id": str(task_id),
            "system": "urge_scan",
        }
        stale_new = (await events_of(session, line_id=stale.line_id))[stale_before:]
        assert _types(stale_new) == [_T.URGE_CLOSED.value]
        assert stale_new[0].actor_id is None
        assert stale_new[0].payload == {
            "urge_task_id": str(stale_task.id),
            "close_reason": "博主已发布",
            "system": "urge_scan",
        }
        _check_registered(due_events + stale_new)


# ---------------------------------------------------------------------------
# 旧召回（S8；召回闭环与运费随 PR-8）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestRecall:
    async def test_start_then_success(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, publish_status="已发布"
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        await svc.start_recall(
            pid, PromotionRecallStartRequest(recall_reason="博主删帖"), flow_users.pr
        )
        shot = await attachment_factory.attachment(purpose="recall_waybill_screenshot")
        promo.recall_waybill_attachment_id = shot.id
        await session.flush()
        await svc.recall_success(
            pid,
            PromotionRecallSuccessRequest(return_shipping_fee=0, return_shipping_payer="到付"),
            flow_users.pr_manager,
        )

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.RECALL_STARTED.value, _T.RECALL_SUCCEEDED.value]
        started, succeeded = events
        assert (started.from_state, started.to_state) == ("未召回", "召回中")
        assert started.note == "博主删帖" and started.actor_id == flow_users.pr.id
        assert (succeeded.from_state, succeeded.to_state) == ("召回中", "召回成功")
        assert succeeded.actor_id == flow_users.pr_manager.id and succeeded.note is None
        assert {e.promotion_id for e in events} == {pid}
        assert all(e.negotiation_id is None for e in events)
        assert started.payload == {} and succeeded.payload == {
            "return_shipping_fee": "0.00",
            "return_shipping_payer": "到付",
        }
        assert succeeded.attachment_id == shot.id
        _check_registered(events)

    async def test_retry_after_failure(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, publish_status="已取消"
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        await svc.start_recall(
            pid, PromotionRecallStartRequest(recall_reason="初次"), flow_users.pr
        )
        await svc.recall_failure(pid, flow_users.pr)
        await svc.start_recall(
            pid, PromotionRecallStartRequest(recall_reason="再试"), flow_users.pr
        )

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [
            _T.RECALL_STARTED.value,
            _T.RECALL_FAILED.value,
            _T.RECALL_STARTED.value,
        ]
        assert events[0].note == "初次"
        assert (events[1].from_state, events[1].to_state) == ("召回中", "召回失败")
        assert (events[2].from_state, events[2].note) == ("召回失败", "再试")


# ---------------------------------------------------------------------------
# 旧推广审核与重提（S8；结款链随 PR-6）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReview:
    async def test_approve_writes_payable_and_destination(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        cross_unit_event_bus: Any,
    ) -> None:
        """应付过服务费规则：财务（经结款单进来）看得到，运营删（6.5 第 3 层，经读接口）。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待核查",
            cooperation_mode="送拍",
            payment_qr=True,
        )
        line_id, pid = promo.line_id, promo.id
        await PromotionService(session).review(
            pid, PromotionReviewRequest(action=ReviewAction.APPROVE), flow_users.pr_manager
        )

        events = await events_of(session, line_id=line_id, event_type=_T.REVIEW_APPROVED)
        assert len(events) == 1
        approved = events[0]
        assert (approved.from_state, approved.to_state) == ("待核查", "待付款")
        assert approved.actor_id == flow_users.pr_manager.id and approved.promotion_id == pid
        assert approved.note is None
        assert approved.payload == {"destination": "待付款", "payable": "500.00"}
        _check_registered(events)

        svc = LineEventService(session)
        finance = {e.event_type: e for e in (await svc.read(line_id, flow_users.finance)).events}
        assert finance["review.approved"].payload == {"destination": "待付款", "payable": "500.00"}
        ops = {e.event_type: e for e in (await svc.read(line_id, flow_users.operations)).events}
        assert ops["review.approved"].payload == {"destination": "待付款"}

    async def test_barter_approve_goes_settled(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待核查",
            cooperation_mode="置换",
            quote_amount=Decimal("0.00"),
        )
        line_id = promo.line_id
        await PromotionService(session).review(
            promo.id, PromotionReviewRequest(action=ReviewAction.APPROVE), flow_users.pr_manager
        )
        # 应付 0 直接结清（R3）：review.approved 之后跟一条 promotion.settled（6.3）
        approved, settled = await events_of(session, line_id=line_id)
        assert approved.event_type == _T.REVIEW_APPROVED.value
        assert approved.to_state == "已结款"
        assert approved.payload == {"destination": "已结款", "payable": "0.00"}
        assert settled.event_type == _T.PROMOTION_SETTLED.value
        assert (settled.from_state, settled.to_state, settled.attachment_id) == (
            "待核查",
            "已结款",
            None,
        )
        assert settled.actor_id == flow_users.pr_manager.id

    async def test_reject_then_resubmit(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        old_date = get_today() - timedelta(days=3)
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待核查",
            publish_url="https://old.example/1",
            actual_publish_date=old_date,
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        reject = PromotionReviewRequest(
            action=ReviewAction.REJECT,
            review_reason="流量太差",
            review_reason_category=RejectReasonCategory.TRAFFIC_REDO,
        )
        await svc.review(pid, reject, flow_users.pr_manager)
        # 第一轮重提改了链接（日期同值不算改）；第二轮什么都没改
        await svc.resubmit(
            pid,
            PromotionResubmitRequest(
                note="已补发", publish_url="https://new.example/2", actual_publish_date=old_date
            ),
            flow_users.pr,
        )
        await svc.review(pid, reject, flow_users.pr_manager)
        await svc.resubmit(pid, PromotionResubmitRequest(note="再看看"), flow_users.pr)

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [
            _T.REVIEW_REJECTED.value,
            _T.REVIEW_RESUBMITTED.value,
            _T.REVIEW_REJECTED.value,
            _T.REVIEW_RESUBMITTED.value,
        ]
        rejected, first, _, second = events
        assert (rejected.from_state, rejected.to_state) == ("待核查", "已驳回")
        assert rejected.note == "流量太差" and rejected.actor_id == flow_users.pr_manager.id
        assert rejected.payload == {"reject_category": "流量差补发"}
        assert (first.from_state, first.to_state) == ("已驳回", "待核查")
        assert first.note == "已补发" and first.actor_id == flow_users.pr.id
        assert first.payload == {
            "diff": {
                "publish_url": {"before": "https://old.example/1", "after": "https://new.example/2"}
            }
        }
        assert second.note == "再看看" and second.payload == {}
        assert {e.promotion_id for e in events} == {pid}
        _check_registered(events)


# ---------------------------------------------------------------------------
# 录 7 天数据与旧复盘（S8；复盘 3 态随 PR-7）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestRetro:
    async def test_written_and_admin_revision_events(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待核查",
            retro_status="待复盘",
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        await svc.write_retrospective(
            pid, RetrospectiveWriteRequest(content="主管版本"), flow_users.pr_manager
        )
        await svc.write_retrospective(
            pid, RetrospectiveWriteRequest(content="管理员修订"), flow_users.admin
        )
        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.RETRO_WRITTEN.value, _T.RETRO_EDITED.value]
        assert (events[0].from_state, events[0].to_state) == ("待复盘", "已完成")
        assert events[0].actor_id == flow_users.pr_manager.id and events[0].note == "主管版本"
        assert (events[1].from_state, events[1].to_state) == ("已完成", "已完成")
        assert events[1].actor_id == flow_users.admin.id and events[1].note == "管理员修订"
        assert events[0].payload["retrospective_id"] != events[1].payload["retrospective_id"]
        _check_registered(events)


# ---------------------------------------------------------------------------
# 上传 / 寄回单号（S9）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestUploads:
    async def test_payment_qr_each_upload_and_bind_writes_one(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """重传两次 → 两条、各挂当次的图（旧图不失联，6.3）；bind 换回旧图 → 第三条；bind 同一张不重复（注入 E_QR）。"""
        from app.core import attachment as att_mod

        monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)
        # 收款码发布后才能传（7.3 ①）：待主管审核的单
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="待核查",
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        first = await svc.upload_payment_qr(
            pid, filename="a.png", mime_type="image/png", data=_PNG, user=flow_users.pr
        )
        second = await svc.upload_payment_qr(
            pid, filename="b.png", mime_type="image/png", data=_PNG, user=flow_users.pr
        )
        first_att, second_att = first.payment_qr_attachment_id, second.payment_qr_attachment_id
        assert first_att is not None and second_att is not None and first_att != second_att
        bind = PromotionPaymentQrBindRequest(payment_qr_attachment_id=first_att)
        await svc.bind_payment_qr(pid, bind, flow_users.pr)
        await svc.bind_payment_qr(pid, bind, flow_users.pr)

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.PROMOTION_PAYMENT_QR_UPLOADED.value] * 3
        assert [e.attachment_id for e in events] == [first_att, second_att, first_att]
        for e in events:
            assert e.promotion_id == pid and e.actor_id == flow_users.pr.id
            assert (e.from_state, e.to_state, e.note) == (None, None, None)
            assert e.payload == {}

    async def test_brand_comment_upload(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core import attachment as att_mod

        monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid, old_att = promo.line_id, promo.id, promo.brand_comment_attachment_id
        resp = await PromotionService(session).upload_brand_comment(
            pid, filename="c.png", mime_type="image/png", data=_PNG, user=flow_users.pr
        )
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_BRAND_COMMENT_UPLOADED.value
        new_att = (
            await session.execute(
                sa_text("SELECT brand_comment_attachment_id FROM promotion WHERE id = :p"),
                {"p": pid},
            )
        ).scalar_one()
        assert new_att is not None and new_att != old_att
        assert event.attachment_id == new_att and resp.id == pid
        assert event.promotion_id == pid and event.actor_id == flow_users.pr.id
        assert event.payload == {}

    async def test_return_waybill_only_when_changed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        for waybill in ("SF100", "SF100", "YT200"):
            await svc.set_return_waybill(
                pid, PromotionReturnWaybillRequest(return_waybill=waybill), flow_users.pr
            )
        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.PROMOTION_RETURN_WAYBILL_SET.value] * 2
        assert [e.payload for e in events] == [
            {"return_waybill": "SF100"},
            {"return_waybill": "YT200"},
        ]
        assert {(e.promotion_id, e.actor_id) for e in events} == {(pid, flow_users.pr.id)}
        _check_registered(events)


# ---------------------------------------------------------------------------
# PATCH / PUT items（S9；D13）
# ---------------------------------------------------------------------------

_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13800000000",
    "receiver_address": "杭州市某路 1 号",
}


@pytest.mark.usefixtures("tenant_ctx")
class TestPatch:
    async def test_remark_and_receiver_split_into_two_events(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """备注进 ``promotion.updated``，收件单独 ``ship.receiver_changed``（updated 里不能有收件，注入 E_PATCH_RECV）。"""
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        line_id, pid = promo.line_id, promo.id
        await PromotionService(session).update_promotion(
            pid,
            PromotionUpdate(
                remark="博主要求周五前到", receiver_name="李四", receiver_phone="13800000000"
            ),
            # PR-5 起备注归合作信息，PR 只读（11-16）：由主管改
            flow_users.pr_manager,
        )
        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.PROMOTION_UPDATED.value, _T.SHIP_RECEIVER_CHANGED.value]
        updated, receiver = events
        assert updated.payload == {
            "diff": {"remark": {"before": None, "after": "博主要求周五前到"}}
        }
        # 电话同值不算改
        assert receiver.payload == {"diff": {"receiver_name": {"before": "张三", "after": "李四"}}}
        for e in events:
            assert e.promotion_id == pid and e.negotiation_id is None
            assert e.actor_id == flow_users.pr_manager.id
            assert (e.from_state, e.to_state) == (None, None)
        _check_registered(events)

        # 读接口：运营看得到发货组，但收件 diff 被裁掉（6.5 第 3 层）；PR 看得到全文
        svc = LineEventService(session)
        ops = {e.event_type: e for e in (await svc.read(line_id, flow_users.operations)).events}
        assert ops["ship.receiver_changed"].payload == {"diff": {}}
        pr = {e.event_type: e for e in (await svc.read(line_id, flow_users.pr)).events}
        assert pr["ship.receiver_changed"].payload == receiver.payload

    async def test_amount_change_lists_field_names_only(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="送拍",
            quote_amount=Decimal("500.00"),
        )
        line_id = promo.line_id
        await PromotionService(session).update_promotion(
            promo.id, PromotionUpdate(quote_amount=Decimal("666.00")), flow_users.pr_manager
        )
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {"amount_fields": ["quote_amount"]}
        assert "666" not in json.dumps(event.payload) and "500" not in json.dumps(event.payload)
        _check_registered([event])

    async def test_no_change_writes_nothing(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        line_id = promo.line_id
        await PromotionService(session).update_promotion(
            promo.id,
            PromotionUpdate(receiver_name="张三", receiver_address=" 杭州市某路 1 号 "),
            flow_users.pr,
        )
        assert await events_of(session, line_id=line_id) == []

    async def test_source_extra_diff_lists_changed_keys_only(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        await svc.update_promotion(
            pid, PromotionUpdate(source_extra={"博主风格": "甜美"}), flow_users.pr
        )
        await svc.update_promotion(
            pid,
            PromotionUpdate(source_extra={"博主风格": "甜美", "寄样备注": "加急"}),
            flow_users.pr,
        )
        first, second = await events_of(session, line_id=line_id)
        assert first.payload == {
            "diff": {"source_extra": {"before": {"博主风格": None}, "after": {"博主风格": "甜美"}}}
        }
        assert second.payload == {
            "diff": {"source_extra": {"before": {"寄样备注": None}, "after": {"寄样备注": "加急"}}}
        }
        _check_registered([first, second])

    async def test_long_remark_is_clipped_not_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """L1：约 3,000 字的备注改成另一段 → 200、落库；diff 前后值各截到 200 字并标出截断（注入 L1_CLIP）。"""
        old, new = "旧" * 3000, "新" * 2999 + "尾"
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, remark=old
        )
        line_id, pid = promo.line_id, promo.id
        resp = await _call(
            session, flow_users.pr_manager, "PATCH", f"/api/promotions/{pid}", {"remark": new}
        )
        assert resp.status_code == 200, resp.text
        stored = (
            await session.execute(
                sa_text("SELECT remark FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        assert stored == new
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {
            "diff": {
                "remark": {
                    "before": "旧" * 200,
                    "after": "新" * 200,
                    "truncated": ["before", "after"],
                }
            }
        }
        _check_registered([event])

    async def test_long_source_extra_value_is_clipped_per_key(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PR-4 收尾 LOW 2：``source_extra`` 一个键 500 字 → 落库全文；diff 里该键的值截到 200 字，
        只有截过的一侧标 ``truncated``（没超 8 KB，不退化成字段名；注入 LOW2_NODICT）。"""
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        long_value = "长" * 499 + "尾"
        await PromotionService(session).update_promotion(
            pid, PromotionUpdate(source_extra={"寄样备注": long_value}), flow_users.pr
        )
        stored = (
            await session.execute(
                sa_text("SELECT source_extra FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        assert stored == {"寄样备注": long_value}
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {
            "diff": {
                "source_extra": {
                    "before": {"寄样备注": None},
                    "after": {"寄样备注": "长" * 200},
                    "truncated": ["after"],
                }
            }
        }
        _check_registered([event])

    async def test_huge_source_extra_falls_back_to_field_names(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """L1：``source_extra`` 20 个键各 1,000 字，截完仍超 8 KB → 200，事件只记字段名、不带值（注入 L1_NAMES）。"""
        promo = await _promo(flow_users, product_factory, blogger_factory, promotion_factory)
        line_id, pid = promo.line_id, promo.id
        extra = {f"自定义{i:02d}": "长" * 1000 for i in range(20)}
        resp = await _call(
            session,
            flow_users.pr_manager,
            "PATCH",
            f"/api/promotions/{pid}",
            {"source_extra": extra, "remark": "补充"},
        )
        assert resp.status_code == 200, resp.text
        stored = (
            await session.execute(
                sa_text("SELECT source_extra FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        assert stored == extra
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {"changed_fields": ["remark", "source_extra"]}
        assert "长" not in json.dumps(event.payload, ensure_ascii=False)
        _check_registered([event])

    @staticmethod
    async def _mode_patch(
        session: AsyncSession, user: Any, promo: Any, body: dict[str, Any]
    ) -> tuple[Any, str | None]:
        """C1：经 HTTP PATCH，再模拟请求结束（没提交的改动随会话回滚丢掉），从库里读合作模式。

        前置数据先 commit、id 先取出来：rollback 之后 ORM 对象全部过期。
        """
        pid = promo.id
        await session.commit()
        resp = await _call(session, user, "PATCH", f"/api/promotions/{pid}", body)
        await session.rollback()
        stored = (
            await session.execute(
                sa_text("SELECT cooperation_mode FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        return resp, stored

    async def test_fill_mode_only_is_persisted_with_event(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """C1：主管只补合作模式（空 → 寄拍）→ 200、落库、写 ``promotion.updated``（diff 带 cooperation_mode，注入 C1_EARLY）。

        PR-4 收尾 LOW 1：补模式连带的成本初始化一起落库 —— 寄拍样品成本置 0、报价不动，
        金额改动记一条「模式初始化」，事件 ``amount_fields`` 只有样品成本。
        """
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode=None,
            cost_snapshot=Decimal("88.00"),
        )
        line_id, pid = promo.line_id, promo.id
        resp, stored = await self._mode_patch(
            session, flow_users.pr_manager, promo, {"cooperation_mode": "寄拍"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["cooperation_mode"] == "寄拍"
        assert stored == "寄拍"
        assert await self._db_costs(session, pid) == (Decimal("500.00"), Decimal("0.00"))
        assert await self._amount_logs(session, pid) == [
            ("cost_snapshot", Decimal("88.00"), Decimal("0.00"), "模式初始化")
        ]
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {
            "diff": {"cooperation_mode": {"before": None, "after": "寄拍"}},
            "amount_fields": ["cost_snapshot"],
        }
        _check_registered([event])

    async def test_fill_barter_only_zeroes_quote_and_inits_cost(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PR-4 收尾 LOW 1：主管只补置换 → 报价落 0、样品成本按商品成员成本初始化，
        两条金额改动都是「模式初始化」，事件 ``amount_fields`` 带报价（注入 LOW1_NOCOST）。"""
        style = await product_factory.style()
        goods = await _goods(session, tenant_a, style, is_suit=False)
        await session.execute(
            sa_text(
                "UPDATE goods_style_item SET single_goods_cost = 61.00 WHERE goods_main_id = :gid"
            ),
            {"gid": goods.id},
        )
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            style=style,
            goods_main_id=goods.id,
            cooperation_mode=None,
            quote_amount=Decimal("500.00"),
        )
        line_id, pid = promo.line_id, promo.id
        resp, stored = await self._mode_patch(
            session, flow_users.pr_manager, promo, {"cooperation_mode": "置换"}
        )
        assert resp.status_code == 200, resp.text
        assert stored == "置换"
        assert await self._db_costs(session, pid) == (Decimal("0.00"), Decimal("61.00"))
        assert await self._amount_logs(session, pid) == [
            ("cost_snapshot", None, Decimal("61.00"), "模式初始化"),
            ("quote_amount", Decimal("500.00"), Decimal("0.00"), "模式初始化"),
        ]
        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.PROMOTION_UPDATED.value
        assert event.payload == {
            "diff": {"cooperation_mode": {"before": None, "after": "置换"}},
            "amount_fields": ["quote_amount", "cost_snapshot"],
        }
        _check_registered([event])

    @staticmethod
    async def _db_costs(session: AsyncSession, pid: Any) -> tuple[Any, Any]:
        row = (
            await session.execute(
                sa_text("SELECT quote_amount, cost_snapshot FROM promotion WHERE id = :pid"),
                {"pid": pid},
            )
        ).one()
        return row[0], row[1]

    @staticmethod
    async def _amount_logs(session: AsyncSession, pid: Any) -> list[tuple[Any, ...]]:
        rows = await session.execute(
            sa_text(
                "SELECT field_name, before_value, after_value, change_source "
                "FROM promotion_amount_log WHERE promotion_id = :pid ORDER BY field_name"
            ),
            {"pid": pid},
        )
        return [tuple(r) for r in rows.all()]

    async def test_fill_mode_with_other_field_still_works(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """C1：合作模式和备注一起补 → 照旧两样都落库，diff 两个键都有。"""
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, cooperation_mode=None
        )
        line_id, pid = promo.line_id, promo.id
        resp, stored = await self._mode_patch(
            session, flow_users.pr_manager, promo, {"cooperation_mode": "送拍", "remark": "补齐"}
        )
        assert resp.status_code == 200, resp.text
        assert stored == "送拍"
        remark = (
            await session.execute(
                sa_text("SELECT remark FROM promotion WHERE id = :pid"), {"pid": pid}
            )
        ).scalar_one()
        assert remark == "补齐"
        (event,) = await events_of(session, line_id=line_id)
        assert event.payload["diff"] == {
            "cooperation_mode": {"before": None, "after": "送拍"},
            "remark": {"before": None, "after": "补齐"},
        }
        _check_registered([event])

    async def test_change_existing_mode_still_409(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """C1：已有值改成别的值照旧 409 ``COOPERATION_MODE_IMMUTABLE``，库里不变、不写事件。"""
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, cooperation_mode="寄拍"
        )
        line_id = promo.line_id
        resp, stored = await self._mode_patch(
            session, flow_users.pr_manager, promo, {"cooperation_mode": "置换"}
        )
        assert (resp.status_code, resp.json()["code"]) == (409, "COOPERATION_MODE_IMMUTABLE")
        assert stored == "寄拍"
        assert await events_of(session, line_id=line_id) == []


class _Suit:
    def __init__(self) -> None:
        self.top: Any = None
        self.pants: Any = None
        self.top_sku: Any = None
        self.top_sku2: Any = None
        self.pants_sku: Any = None
        self.goods: Any = None
        self.single: Any = None


@pytest.fixture
async def suit(session: AsyncSession, tenant_a: Any, product_factory: Any) -> _Suit:
    """上衣（简称「上衣」）+ 裤子（无简称 → 用全称）的套装，另有只含上衣的单品商品。"""
    s = _Suit()
    s.top = await product_factory.style(short_name="上衣", style_name="条纹上衣全称")
    s.pants = await product_factory.style(short_name=None, style_name="阔腿裤")
    s.top_sku = await product_factory.sku(s.top, color="黑色", size="M")
    s.top_sku2 = await product_factory.sku(s.top, color="灰色", size="S")
    s.pants_sku = await product_factory.sku(s.pants, color="白色", size="L")
    s.goods = await _goods(session, tenant_a, s.top, s.pants, is_suit=True)
    s.single = await _goods(session, tenant_a, s.top, is_suit=False)
    return s


def _no_codes(payload: Any, *codes: str) -> None:
    dumped = json.dumps(payload, ensure_ascii=False)
    for code in codes:
        assert code not in dumped


@pytest.mark.usefixtures("tenant_ctx")
class TestItems:
    async def _single(
        self, flow_users: Any, blogger_factory: Any, promotion_factory: Any, suit: _Suit, **kw: Any
    ) -> Any:
        blogger = await blogger_factory.blogger()
        return await promotion_factory.promotion(
            style=suit.top,
            blogger=blogger,
            pr=flow_users.pr,
            goods_main_id=suit.single.id,
            sku_id=suit.top_sku.id,
            items=[(suit.top, suit.top_sku)],
            **kw,
        )

    async def test_change_goods_before_push_writes_items_changed(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        """推送前换商品删明细也记一条（6.3；注入 E_ITEMS_GOODS）。"""
        promo = await self._single(
            flow_users, blogger_factory, promotion_factory, suit, ship_status="待发货"
        )
        line_id = promo.line_id
        await PromotionService(session).update_promotion(
            promo.id, PromotionUpdate(goods_main_id=suit.goods.id), flow_users.pr_manager
        )
        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.PROMOTION_UPDATED.value, _T.SHIP_ITEMS_CHANGED.value]
        updated, items = events
        assert updated.payload == {
            "diff": {"goods_main_id": {"before": str(suit.single.id), "after": str(suit.goods.id)}}
        }
        assert items.payload == {
            "items_before": [["上衣", "黑色", "M"]],
            "items_after": [],
            "reason": "换归属商品，推送前的明细已清空",
        }
        assert items.actor_id == flow_users.pr_manager.id and items.promotion_id == promo.id
        _no_codes(items.payload, suit.top.style_code, suit.top_sku.sku_code)
        _check_registered(events)

    async def test_change_goods_after_push_keeps_items(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        promo = await self._single(
            flow_users, blogger_factory, promotion_factory, suit, ship_status="待打单"
        )
        line_id = promo.line_id
        await PromotionService(session).update_promotion(
            promo.id, PromotionUpdate(goods_main_id=suit.goods.id), flow_users.pr_manager
        )
        assert _types(await events_of(session, line_id=line_id)) == [_T.PROMOTION_UPDATED.value]

    async def test_patch_sku_writes_items_changed(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        suit: _Suit,
    ) -> None:
        promo = await self._single(
            flow_users, blogger_factory, promotion_factory, suit, ship_status="待发货"
        )
        line_id = promo.line_id
        await PromotionService(session).update_promotion(
            promo.id, PromotionUpdate(sku_id=suit.top_sku2.id), flow_users.pr_manager
        )
        updated, items = await events_of(session, line_id=line_id)
        assert updated.event_type == _T.PROMOTION_UPDATED.value
        assert updated.payload == {
            "diff": {"sku_id": {"before": str(suit.top_sku.id), "after": str(suit.top_sku2.id)}}
        }
        assert items.event_type == _T.SHIP_ITEMS_CHANGED.value
        assert items.payload == {
            "items_before": [["上衣", "黑色", "M"]],
            "items_after": [["上衣", "灰色", "S"]],
            "reason": "改主款颜色尺码",
        }
        _check_registered([updated, items])

    async def test_put_items_writes_once_and_same_items_write_nothing(
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
            ship_status="待发货",
        )
        line_id, pid = promo.line_id, promo.id
        items = [
            GoodsItemIn(style_id=suit.pants.id, sku_id=suit.pants_sku.id),
            GoodsItemIn(style_id=suit.top.id, sku_id=suit.top_sku.id),
        ]
        svc = PromotionService(session)
        await svc.replace_items(pid, items, flow_users.pr_manager)
        await svc.replace_items(pid, items, flow_users.pr_manager)

        (event,) = await events_of(session, line_id=line_id)
        assert event.event_type == _T.SHIP_ITEMS_CHANGED.value
        assert event.payload == {
            "items_before": [],
            "items_after": [["阔腿裤", "白色", "L"], ["上衣", "黑色", "M"]],
            "reason": "改颜色尺码明细",
        }
        assert event.actor_id == flow_users.pr_manager.id and event.promotion_id == pid
        assert (event.from_state, event.to_state) == (None, None)
        _no_codes(event.payload, suit.pants.style_code, suit.pants_sku.sku_code)
        _check_registered([event])
