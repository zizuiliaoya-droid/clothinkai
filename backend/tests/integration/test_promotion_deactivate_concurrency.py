"""停用与审核建单的真实事务交错（PR-6 收尾评审 M1）。

删掉停用的行互斥会让审核先建单后仍被停用；反向交错会让审核越过尚未提交的停用。
两条业务连接用事件暂停，第三条只读连接通过 pg_blocking_pids 证明等待，不能靠 sleep 猜顺序。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Tenant, User
from app.modules.finance.schemas import SettlementPaymentAmountRequest, SettlementReviewRequest
from app.modules.finance.service import SettlementService
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import PromotionReviewRequest, PromotionUpdate
from app.modules.promotion.service import PromotionService
from tests.conftest import purge_promotions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session(engine: Any) -> AsyncIterator[AsyncSession]:
    """此文件造数要真实提交，覆盖常规单连接 savepoint fixture。"""
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s


@pytest_asyncio.fixture
async def tenant_a(session: AsyncSession) -> AsyncIterator[Tenant]:
    tenant = Tenant(id=uuid4(), code=f"deact_{uuid4().hex[:8]}", name="停用并发测试")
    session.add(tenant)
    await session.flush()
    tid = tenant.id
    try:
        yield tenant
    finally:
        await session.rollback()
        pids = (
            await session.execute(select(Promotion.id).where(Promotion.tenant_id == tid))
        ).scalars()
        await purge_promotions(session, list(pids))
        # 只清本测试租户；角色和权限是迁移种子，不能删。
        for table in (
            "audit_log",
            "settlement_sequence",
            "user_role",
            "attachment",
            "blogger",
            "style",
            '"user"',
            "tenant",
        ):
            column = "id" if table == "tenant" else "tenant_id"
            await session.execute(text(f"DELETE FROM {table} WHERE {column} = :tid"), {"tid": tid})
        await session.commit()


async def _attempt(s: AsyncSession, call: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await call()
    except AppException as exc:
        await s.rollback()
        return exc


async def _wait_for_blocker(
    maker: async_sessionmaker[AsyncSession], task: asyncio.Task[Any], waiter: int, holder: int
) -> None:
    async with asyncio.timeout(10), maker() as probe:
        while True:
            blockers = await probe.scalar(
                text("SELECT pg_blocking_pids(CAST(:pid AS integer))"), {"pid": waiter}
            )
            if holder in blockers:
                return
            assert not task.done(), "后来的请求未等先到事务提交就已完成"


async def _state(s: AsyncSession, pid: UUID) -> tuple[Any, ...]:
    row = (
        await s.execute(
            text(
                "SELECT p.is_active, p.settlement_status, "
                "(SELECT COUNT(*) FROM settlement WHERE promotion_id = p.id), "
                "(SELECT settlement_status FROM settlement WHERE promotion_id = p.id), "
                "(SELECT COUNT(*) FROM line_event WHERE promotion_id = p.id "
                "AND event_type = 'promotion.updated'), "
                "(SELECT COUNT(*) FROM line_event WHERE promotion_id = p.id "
                "AND event_type = 'review.approved'), "
                "(SELECT COUNT(*) FROM line_event WHERE promotion_id = p.id "
                "AND event_type = 'settlement.created') FROM promotion p WHERE p.id = :pid"
            ),
            {"pid": pid},
        )
    ).one()
    return tuple(row)


@pytest.mark.usefixtures("cross_unit_event_bus")
@pytest.mark.parametrize("via", ["delete", "patch"])
@pytest.mark.parametrize(
    ("first", "deactivator"),
    [("review", "admin"), ("deactivate", "admin"), ("review", "pr_manager")],
)
async def test_deactivate_and_review_are_serialized(
    engine: Any,
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    attachment_factory: Any,
    promotion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    via: str,
    first: str,
    deactivator: str,
) -> None:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    qr = await attachment_factory.attachment(purpose="payment_qr", created_by=flow_users.pr.id)
    promo = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        internal_code=f"DEACT{uuid4().hex}",
        cooperation_mode="送拍",
        publish_status="已发布",
        settlement_status="待核查",
        publish_url="https://example.test/post",
        actual_publish_date=date(2026, 10, 1),
        quote_amount=Decimal("500.00"),
    )
    promo.payment_qr_attachment_id = qr.id
    pid, tid = promo.id, flow_users.tenant.id
    await session.commit()  # 独立业务连接必须看得到真实已提交的造数。
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    token = tenant_id_ctx.set(tid)
    held, release = asyncio.Event(), asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []
    try:
        async with maker() as review_s, maker() as deactivate_s:
            reviewer = await review_s.get(User, flow_users.pr_manager.id)
            admin = await deactivate_s.get(User, getattr(flow_users, deactivator).id)
            assert reviewer is not None and admin is not None
            review_pid = int(await review_s.scalar(text("SELECT pg_backend_pid()")))
            deactivate_pid = int(await deactivate_s.scalar(text("SELECT pg_backend_pid()")))
            assert review_pid != deactivate_pid
            review_service, deactivate_service = (
                PromotionService(review_s),
                PromotionService(deactivate_s),
            )
            # 故意缓存锁前实体，锁等待后必须刷新，不能靠 identity map 的旧快照判矩阵。
            cached = await deactivate_service._repo.get_by_id(pid)
            assert cached is not None and cached.settlement_status == "待核查"

            if first == "review":
                original_update = review_service._repo.update_state

                async def pause_after_review_update(**kwargs: Any) -> Any:
                    updated = await original_update(**kwargs)
                    assert updated is not None
                    held.set()  # 真实 UPDATE 已持推广行锁，事件派发/建单还没做。
                    await release.wait()
                    return updated

                monkeypatch.setattr(review_service._repo, "update_state", pause_after_review_update)
            else:
                original_gate = deactivate_service._ensure_no_settlement_in_progress

                async def pause_after_deactivate_gate(promotion: Promotion) -> None:
                    await original_gate(promotion)
                    held.set()  # 停用已通过真实空结款检查，尚未写 is_active。
                    await release.wait()

                monkeypatch.setattr(
                    deactivate_service,
                    "_ensure_no_settlement_in_progress",
                    pause_after_deactivate_gate,
                )

            async def review() -> Any:
                return await _attempt(
                    review_s,
                    lambda: review_service.review(
                        pid, PromotionReviewRequest(action=ReviewAction.APPROVE), reviewer
                    ),
                )

            async def deactivate() -> Any:
                return await _attempt(
                    deactivate_s,
                    lambda: (
                        deactivate_service.soft_delete_promotion(pid, admin)
                        if via == "delete"
                        else deactivate_service.update_promotion(
                            pid, PromotionUpdate(is_active=False), admin
                        )
                    ),
                )

            try:
                leading = asyncio.create_task(review() if first == "review" else deactivate())
                tasks.append(leading)
                await asyncio.wait_for(held.wait(), timeout=10)
                following = asyncio.create_task(deactivate() if first == "review" else review())
                tasks.append(following)
                holder, waiter = (
                    (review_pid, deactivate_pid)
                    if first == "review"
                    else (deactivate_pid, review_pid)
                )
                await _wait_for_blocker(maker, following, waiter, holder)
                release.set()
                lead_result, follow_result = await asyncio.wait_for(
                    asyncio.gather(leading, following), timeout=10
                )
            finally:
                release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        assert not isinstance(lead_result, AppException)
        assert isinstance(follow_result, AppException)
        assert follow_result.status_code == (403 if deactivator == "pr_manager" else 409)
        async with maker() as check:
            if first == "review":
                assert follow_result.code == (
                    ("PERMISSION_DENIED" if via == "delete" else "FIELD_PERMISSION_DENIED")
                    if deactivator == "pr_manager"
                    else "PROMOTION_SETTLEMENT_IN_PROGRESS"
                )
                assert lead_result.review_outcome == "settlement_created"
                assert await _state(check, pid) == (True, "待付款", 1, "待付款", 0, 1, 1)
                # 不只检查状态：新建的进行中单必须仍能由财务填实付并驳回。
                sid = await check.scalar(
                    text("SELECT id FROM settlement WHERE promotion_id = :p"), {"p": pid}
                )
                finance = await check.get(User, flow_users.finance.id)
                assert finance is not None
                finance_service = SettlementService(check)
                await finance_service.fill_payment_amount(
                    sid, SettlementPaymentAmountRequest(payment_amount=Decimal("500.00")), finance
                )
                await finance_service.review(
                    sid,
                    SettlementReviewRequest(action="reject", review_reason="并发测试驳回"),
                    finance,
                )
                assert await _state(check, pid) == (True, "结款驳回", 1, "已驳回", 0, 1, 1)
            else:
                assert follow_result.code == "PROMOTION_STATE_CONFLICT"
                assert await _state(check, pid) == (False, "待核查", 0, None, 1, 0, 0)
                audits = (
                    (
                        await check.execute(
                            text(
                                "SELECT action FROM audit_log WHERE resource_id = :p ORDER BY created_at"
                            ),
                            {"p": str(pid)},
                        )
                    )
                    .scalars()
                    .all()
                )
                # PATCH is_active 本来只写时间线（审计仅敏感字段），不能假设有 promotion.update。
                assert audits == (["promotion.delete"] if via == "delete" else [])
    finally:
        tenant_id_ctx.reset(token)
