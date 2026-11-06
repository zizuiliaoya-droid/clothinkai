"""发布事实修改与结款检查共用主单锁，不能检查后穿透。"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException, ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.blogger.account_schemas import AccountIdentity, AccountSnapshot
from app.modules.finance.schemas import SettlementPaymentProofRequest, SettlementReviewRequest
from app.modules.finance.service import SettlementService
from app.modules.promotion import fulfillment
from app.modules.promotion.fulfillment import (
    account_posts,
    initialize_account_posts,
    require_fulfilled,
)
from app.modules.promotion.schemas import PlatformPostIn
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.integration.test_promotion_content_platform import promotion
from tests.integration.test_promotion_deactivate_concurrency import (  # noqa: F401
    _attempt,
    _wait_for_blocker,
    session,
    tenant_a,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_settlement_checker_waits_for_account_fact_writer(
    engine,
    session,  # noqa: F811
    flow_users,
    product_factory,
    blogger_factory,
    promotion_factory,
    monkeypatch,
):
    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    other = await blogger_factory.blogger()
    p.account_snapshot = AccountSnapshot(
        primary_account_id=p.blogger_id,
        accounts=[
            AccountIdentity(
                account_id=p.blogger_id, platform=p.platform, account="primary", nickname="主账号"
            ),
            AccountIdentity(
                account_id=other.id, platform=other.platform, account="second", nickname="副账号"
            ),
        ],
        price_source="manual",
    ).model_dump(mode="json")
    await session.flush()
    await initialize_account_posts(session, p, flow_users.pr.id)
    rows = await account_posts(session, p)
    for row in rows:
        row.publish_url, row.actual_publish_date = "https://example.test/published", get_today()
    inputs = [
        PlatformPostIn(
            account_id=row.account_id,
            platform=row.platform,
            publish_url=row.publish_url,
            actual_publish_date=row.actual_publish_date,
        )
        for row in rows
    ]
    inputs[1].publish_url = None
    pid, tid, uid = p.id, p.tenant_id, flow_users.pr.id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    tasks = []
    token = tenant_id_ctx.set(tid)
    try:
        async with maker() as first, maker() as second:
            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            user = await first.get(User, uid)
            service = PromotionService(first)
            original = service._repo.get_for_write

            async def hold(*args, **kwargs):
                result = await original(*args, **kwargs)
                held.set()
                await asyncio.wait_for(release.wait(), 10)
                return result

            monkeypatch.setattr(service._repo, "get_for_write", hold)
            tasks.append(
                asyncio.create_task(
                    _attempt(first, lambda: service.replace_platform_posts(pid, inputs, user))
                )
            )
            await asyncio.wait_for(held.wait(), 10)
            tasks.append(
                asyncio.create_task(_attempt(second, lambda: require_fulfilled(second, pid, tid)))
            )
            await _wait_for_blocker(maker, tasks[1], second_pid, first_pid)
            release.set()
            result = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert not isinstance(result[0], Exception)
            assert isinstance(result[1], ValidationError)
            assert result[1].code == "ACCOUNT_FULFILLMENT_REQUIRED"
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tenant_id_ctx.reset(token)


@pytest.mark.usefixtures("cross_unit_event_bus")
async def test_payment_and_rejection_lock_promotion_before_settlement(
    engine,
    session,  # noqa: F811
    flow_users,
    product_factory,
    blogger_factory,
    promotion_factory,
    settlement_factory,
    attachment_factory,
    monkeypatch,
):
    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    p.settlement_status = "待付款"
    p.account_snapshot = AccountSnapshot(
        primary_account_id=p.blogger_id,
        accounts=[
            AccountIdentity(
                account_id=p.blogger_id, platform=p.platform, account="primary", nickname="主账号"
            )
        ],
        price_source="manual",
    ).model_dump(mode="json")
    await initialize_account_posts(session, p, flow_users.pr.id)
    row = (await account_posts(session, p))[0]
    row.publish_url, row.actual_publish_date = "https://example.test/published", get_today()
    settlement = await settlement_factory.settlement(
        style=SimpleNamespace(id=p.style_id),
        blogger=SimpleNamespace(id=p.blogger_id),
        promotion=p,
        pr=flow_users.pr,
        settlement_status="待财务付款",
        payment_amount=Decimal("500"),
    )
    proof = await attachment_factory.attachment(created_by=flow_users.finance.id)
    sid, tid, uid, proof_id = settlement.id, p.tenant_id, flow_users.finance.id, proof.id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    tasks = []
    token = tenant_id_ctx.set(tid)
    try:
        async with maker() as payment_session, maker() as reject_session:
            payment_pid = await payment_session.scalar(text("SELECT pg_backend_pid()"))
            reject_pid = await reject_session.scalar(text("SELECT pg_backend_pid()"))
            payer = await payment_session.get(User, uid)
            reviewer = await reject_session.get(User, uid)
            original = fulfillment.lock_promotion

            async def hold_payment(s, promotion_id, tenant_id):
                result = await original(s, promotion_id, tenant_id)
                if s is payment_session:
                    held.set()
                    await asyncio.wait_for(release.wait(), 10)
                return result

            monkeypatch.setattr(fulfillment, "lock_promotion", hold_payment)
            payment_service = SettlementService(payment_session)
            reject_service = SettlementService(reject_session)
            tasks.append(
                asyncio.create_task(
                    _attempt(
                        payment_session,
                        lambda: payment_service.upload_payment_proof(
                            sid,
                            SettlementPaymentProofRequest(payment_proof_attachment_id=proof_id),
                            payer,
                        ),
                    )
                )
            )
            await asyncio.wait_for(held.wait(), 10)
            tasks.append(
                asyncio.create_task(
                    _attempt(
                        reject_session,
                        lambda: reject_service.review(
                            sid,
                            SettlementReviewRequest(action="reject", review_reason="同时驳回"),
                            reviewer,
                        ),
                    )
                )
            )
            await _wait_for_blocker(maker, tasks[1], reject_pid, payment_pid)
            release.set()
            paid, rejected = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert not isinstance(paid, AppException)
            assert paid.settlement_status == "已付款"
            assert isinstance(rejected, AppException) and rejected.status_code == 409
            async with maker() as check:
                status = await check.scalar(
                    text("SELECT settlement_status FROM settlement WHERE id=:id"), {"id": sid}
                )
                assert status == "已付款"
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tenant_id_ctx.reset(token)
