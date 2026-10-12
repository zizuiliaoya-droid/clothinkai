"""给现有推广单动作补写的主线事件（流程线设计 6.3、6.4；PR-4 S7 起逐条加）。

守的不变量：
- 每个动作至少一条「写了事件」断言：类型、挂哪边、from / to、note、附件、payload（键都已登记，``assert_payload_registered``）
- 同事务（6.4，G6）：动作在写事件之后失败 → 事件跟着回滚（注入 G6：append 里 commit）
- 事件写失败就让动作失败（6.4、11-49）：发布顺手关催发任务那段吞异常，``urge.closed`` 不能挪进去（注入 E_SWALLOW）
- 系统写的事件 ``actor_id = NULL``、payload 带 ``system``（自动扫描 = ``urge_scan``）；自动催发不逐天记（11-22）

S7：发布、取消（待打单多一条 ``ship.withdrawn``）、催发（手动催 / 批量催 / 手动关闭 / 扫描建任务与收口）。
S8：旧召回（发起 / 成功 / 失败）、旧推广审核（通过 / 驳回）与重提、录 7 天数据、旧复盘（写 / 确认 / 打回）。
"""

from __future__ import annotations

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
    PromotionCancelRequest,
    PromotionMetricsRequest,
    PromotionPublishRequest,
    PromotionRecallStartRequest,
    PromotionResubmitRequest,
    PromotionReviewRequest,
    RetrospectiveConfirmRequest,
    RetrospectiveSubmitRequest,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.service import UrgeService
from tests.flow_event_helpers import assert_payload_registered, events_of

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
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, publish_status="已发布"
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)
        await svc.start_recall(
            pid, PromotionRecallStartRequest(recall_reason="博主删帖"), flow_users.pr
        )
        await svc.recall_success(pid, flow_users.pr_manager)

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [_T.RECALL_STARTED.value, _T.RECALL_SUCCEEDED.value]
        started, succeeded = events
        assert (started.from_state, started.to_state) == ("未召回", "召回中")
        assert started.note == "博主删帖" and started.actor_id == flow_users.pr.id
        assert (succeeded.from_state, succeeded.to_state) == ("召回中", "召回成功")
        assert succeeded.actor_id == flow_users.pr_manager.id and succeeded.note is None
        assert {e.promotion_id for e in events} == {pid}
        assert all(e.negotiation_id is None and e.payload == {} for e in events)

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
        await svc.start_recall(pid, PromotionRecallStartRequest(), flow_users.pr)
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
        assert events[0].note is None
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

    async def test_barter_approve_goes_paid(
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
        (approved,) = await events_of(session, line_id=line_id)
        assert approved.event_type == _T.REVIEW_APPROVED.value
        assert approved.to_state == "已付款"
        assert approved.payload == {"destination": "已付款", "payable": "0.00"}

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
    async def test_metrics_written_returned_confirmed(
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
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            publish_status="已发布",
            settlement_status="已付款",
        )
        line_id, pid = promo.line_id, promo.id
        svc = PromotionService(session)

        await svc.record_metrics(
            pid,
            PromotionMetricsRequest(like_count=120, collect_count=30, comment_count=8),
            flow_users.pr,
            screenshot=("m.png", "image/png", _PNG),
        )
        await svc.submit_retrospective(
            pid, RetrospectiveSubmitRequest(content="第一版复盘"), flow_users.pr
        )
        await svc.confirm_retrospective(
            pid,
            RetrospectiveConfirmRequest(approve=False, opinion="写细一点"),
            flow_users.pr_manager,
        )
        await svc.submit_retrospective(
            pid, RetrospectiveSubmitRequest(content="第二版复盘"), flow_users.pr
        )
        await svc.confirm_retrospective(
            pid, RetrospectiveConfirmRequest(approve=True), flow_users.pr_manager
        )

        events = await events_of(session, line_id=line_id)
        assert _types(events) == [
            _T.PROMOTION_METRICS_RECORDED.value,
            _T.RETRO_WRITTEN.value,
            _T.RETRO_RETURNED.value,
            _T.RETRO_WRITTEN.value,
            _T.RETRO_CONFIRMED.value,
        ]
        metrics, written1, returned, written2, confirmed = events
        metrics_att = (
            await session.execute(
                sa_text("SELECT metrics_attachment_id FROM promotion WHERE id = :p"), {"p": pid}
            )
        ).scalar_one()
        assert metrics_att is not None and metrics.attachment_id == metrics_att
        assert (metrics.from_state, metrics.to_state) == ("未开始", "待复盘")
        assert metrics.payload == {"like_count": 120, "collect_count": 30, "comment_count": 8}
        assert metrics.actor_id == flow_users.pr.id

        retro_ids = {
            content: str(rid)
            for rid, content in (
                await session.execute(
                    sa_text(
                        "SELECT id, content FROM blogger_retrospective WHERE promotion_id = :p"
                    ),
                    {"p": pid},
                )
            ).all()
        }
        assert (written1.from_state, written1.to_state) == ("待复盘", "待确认")
        assert written1.note == "第一版复盘" and written1.actor_id == flow_users.pr.id
        assert written1.payload == {"retrospective_id": retro_ids["第一版复盘"]}
        assert (returned.from_state, returned.to_state) == ("待确认", "待复盘")
        assert returned.note == "写细一点" and returned.actor_id == flow_users.pr_manager.id
        assert returned.payload == {"retrospective_id": retro_ids["第一版复盘"]}
        assert written2.payload == {"retrospective_id": retro_ids["第二版复盘"]}
        assert (confirmed.from_state, confirmed.to_state) == ("待确认", "已完成")
        assert confirmed.note is None
        assert confirmed.payload == {"retrospective_id": retro_ids["第二版复盘"]}
        assert {e.promotion_id for e in events} == {pid}
        _check_registered(events)
