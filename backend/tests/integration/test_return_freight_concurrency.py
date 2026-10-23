"""独立PG连接真实锁等待，验证R33唯一与快照/付款/停用交错。"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.modules.auth.models import User
from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.freight_schemas import (
    FreightRejectRequest,
    FreightResubmitRequest,
    FreightReviewRequest,
    FreightSubmitRequest,
)
from app.modules.promotion.freight_service import ReturnFreightService
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionRecallSuccessRequest, PromotionUpdate
from app.modules.promotion.service import PromotionService
from tests.integration import test_promotion_deactivate_concurrency as committed
from tests.integration import test_return_freight as freight_fixtures
from tests.integration import test_settlement_proof_upload as storage_fixtures

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
session = committed.session
tenant_a = committed.tenant_a
freight_tenant = freight_fixtures.freight_tenant
freight_promotion = freight_fixtures.freight_promotion
r2 = storage_fixtures.r2


@pytest.mark.parametrize(
    "pair",
    [
        "submit_submit",
        "success_submit",
        "resubmit_review",
        "pay_reject",
        "review_waybill",
        "submit_deactivate",
        "deactivate_submit",
        "pay_deactivate",
        "pay_delete",
    ],
)
async def test_two_connections_serialize_freight_actions(
    engine, session, freight_promotion, flow_users, monkeypatch, r2, pair
):
    p = freight_promotion
    first, second = pair.split("_")
    fid = None
    if first in ("resubmit", "pay", "review"):
        f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
        fid = f.id
        f.status = {"resubmit": "已驳回", "pay": "待付款", "review": "待审核"}[first]
    if first == "success":
        p.recall_status = "召回中"
    pid = p.id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    tasks = []
    async with maker() as a, maker() as b:
        aid, bid = (
            await a.scalar(text("SELECT pg_backend_pid()")),
            await b.scalar(text("SELECT pg_backend_pid()")),
        )
        assert aid != bid
        # 显式缓存旧付款单，让锁后必须刷新，而不是刚好没有identity-map缓存。
        cached = await b.get(ReturnFreightPayment, fid) if fid else None
        assert cached is None or cached.id == fid
        if first == "pay":
            assert cached is not None and cached.status == "待付款"
        original = PromotionRepository.get_for_write

        async def pause_after_lock(repo, *args, **kwargs):
            result = await original(repo, *args, **kwargs)
            if repo._session is a and not held.is_set():
                held.set()
                await release.wait()
            return result

        monkeypatch.setattr(PromotionRepository, "get_for_write", pause_after_lock)

        async def invoke(s, action):
            who = (
                "finance"
                if action in ("pay", "reject")
                else "pr_manager"
                if action == "review"
                else "admin"
                if action in ("deactivate", "delete")
                else "pr"
            )
            user = await s.get(User, getattr(flow_users, who).id)
            freight, promotion = ReturnFreightService(s), PromotionService(s)

            async def call():
                if action == "submit":
                    return await freight.submit(pid, FreightSubmitRequest(), user)
                if action == "success":
                    return await promotion.recall_success(
                        pid,
                        PromotionRecallSuccessRequest(
                            return_shipping_fee="30", return_shipping_payer="博主垫付"
                        ),
                        user,
                    )
                if action == "resubmit":
                    return await freight.resubmit(
                        fid, FreightResubmitRequest(note="金额核实", amount="35"), user
                    )
                if action == "review":
                    return await freight.review(fid, FreightReviewRequest(action="approve"), user)
                if action == "pay":
                    return await freight.pay(
                        fid,
                        filename="pay.png",
                        mime_type="image/png",
                        data=storage_fixtures._PNG,
                        user=user,
                    )
                if action == "reject":
                    return await freight.reject_payment(
                        fid, FreightRejectRequest(reason="退款核实"), user
                    )
                if action == "waybill":
                    return await promotion.upload_recall_waybill(
                        pid,
                        filename="wb.png",
                        mime_type="image/png",
                        data=storage_fixtures._PNG,
                        user=user,
                    )
                if action == "delete":
                    return await promotion.soft_delete_promotion(pid, user)
                return await promotion.update_promotion(pid, PromotionUpdate(is_active=False), user)

            return await committed._attempt(s, call)

        try:
            leading = asyncio.create_task(invoke(a, first))
            tasks.append(leading)
            await asyncio.wait_for(held.wait(), 10)
            following = asyncio.create_task(invoke(b, second))
            tasks.append(following)
            await committed._wait_for_blocker(maker, following, bid, aid)
            release.set()
            lead, follow = await asyncio.wait_for(asyncio.gather(leading, following), 10)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert not isinstance(lead, AppException)
    both_succeed = pair in ("resubmit_review", "pay_deactivate", "pay_delete")
    assert isinstance(follow, AppException) is not both_succeed
    if isinstance(follow, AppException):
        assert follow.status_code in (403, 409, 422), follow
    async with maker() as probe:
        current = await probe.get(Promotion, pid)
        rows = list(
            await probe.scalars(
                select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == pid)
            )
        )
        if pair == "deactivate_submit":
            assert not current.is_active and not rows
        else:
            assert len(rows) == 1
            expected = (
                "待付款"
                if first in ("review", "resubmit")
                else "已付款"
                if first == "pay"
                else "待审核"
            )
            assert rows[0].status == expected
            assert (
                current.return_shipping_fee
                == rows[0].amount
                == Decimal("35" if first == "resubmit" else "30")
            )
            assert current.is_active is (pair not in ("pay_deactivate", "pay_delete"))
        if first == "pay":
            assert len(r2.puts) == 1
