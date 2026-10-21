"""已提交造数、独立事务与新连接读取，证明失败不会留下半笔召回或运费。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository
from app.modules.promotion.freight_schemas import FreightResubmitRequest, FreightSubmitRequest
from app.modules.promotion.freight_service import ReturnFreightService
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionRecallStartRequest, PromotionRecallSuccessRequest
from app.modules.promotion.service import PromotionService
from app.modules.urge.models import UrgeTask
from app.modules.urge.service import UrgeService
from tests.integration import test_promotion_deactivate_concurrency as committed
from tests.integration import test_return_freight as freight_fixtures
from tests.integration import test_settlement_proof_upload as storage_fixtures

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
session = committed.session
tenant_a = committed.tenant_a
freight_tenant = freight_fixtures.freight_tenant
freight_promotion = freight_fixtures.freight_promotion
r2 = storage_fixtures.r2


async def state(maker, pid):
    async with maker() as probe:
        return tuple(
            (
                await probe.execute(
                    text("""
            SELECT p.publish_status, p.recall_status, p.return_shipping_fee,
                   p.return_shipping_payer, p.completed_at,
                   (SELECT jsonb_agg(to_jsonb(f)) FROM return_freight_payment f WHERE f.promotion_id=p.id),
                   (SELECT count(*) FROM promotion_amount_log a WHERE a.promotion_id=p.id),
                   (SELECT count(*) FROM line_event e WHERE e.promotion_id=p.id),
                   (SELECT status FROM urge_task u WHERE u.promotion_id=p.id),
                   (SELECT count(*) FROM attachment a WHERE a.tenant_id=p.tenant_id)
            FROM promotion p WHERE p.id=:pid
        """),
                    {"pid": pid},
                )
            ).one()
        )


@pytest.mark.parametrize(
    "point",
    ["c2_urge", "c2_event", "c4_auto", "c4_complete", "c4_completed_auto", "t6_event", "t4_event"],
)
async def test_failure_rolls_back_entire_freight_transaction(
    engine, session, freight_promotion, flow_users, monkeypatch, r2, point
):
    p = freight_promotion
    fid = None
    if point.startswith("c2"):
        p.publish_status, p.recall_status, p.ship_status = "未发布", "未召回", "已发货"
        session.add(
            UrgeTask(
                tenant_id=p.tenant_id,
                promotion_id=p.id,
                blogger_id=p.blogger_id,
                pr_id=p.pr_id,
                status="进行中",
            )
        )
    elif point.startswith("c4"):
        p.recall_status, p.return_shipping_fee, p.return_shipping_payer = "召回中", None, None
        if point == "c4_completed_auto":
            p.publish_status, p.completed_at = "已发布", datetime.now(UTC)
    else:
        f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
        fid = f.id
        f.status = "已驳回" if point == "t6_event" else "待付款"
        p.completed_at = datetime.now(UTC)
    pid, uid = p.id, (flow_users.finance.id if point == "t4_event" else flow_users.pr.id)
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    before = await state(maker, pid)

    async def fail_after(original, *args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected-after-write")

    if point == "c2_urge":
        original = UrgeService.close_for_promotion
        monkeypatch.setattr(
            UrgeService, "close_for_promotion", lambda *a, **kw: fail_after(original, *a, **kw)
        )
    elif point in ("c4_auto", "c4_completed_auto"):
        original = ReturnFreightService.submit_locked
        monkeypatch.setattr(
            ReturnFreightService, "submit_locked", lambda *a, **kw: fail_after(original, *a, **kw)
        )
    elif point == "c4_complete":
        original = PromotionRepository.maybe_complete
        monkeypatch.setattr(
            PromotionRepository, "maybe_complete", lambda *a, **kw: fail_after(original, *a, **kw)
        )
    else:
        original = LineEventRepository.append

        async def fail_event(*args, **kwargs):
            result = await original(*args, **kwargs)
            event = kwargs["event_type"]
            if getattr(event, "value", event) in (
                {"recall.started"}
                if point == "c2_event"
                else {"freight.resubmitted", "freight.paid"}
            ):
                raise RuntimeError("injected-after-write")
            return result

        monkeypatch.setattr(LineEventRepository, "append", fail_event)

    async with maker() as writer:
        user = await writer.get(User, uid)
        with pytest.raises(RuntimeError, match="injected-after-write"):
            try:
                if point.startswith("c2"):
                    await PromotionService(writer).start_recall(
                        pid, PromotionRecallStartRequest(recall_reason="需要召回"), user
                    )
                elif point.startswith("c4"):
                    await PromotionService(writer).recall_success(
                        pid,
                        PromotionRecallSuccessRequest(
                            return_shipping_fee="30", return_shipping_payer="博主垫付"
                        ),
                        user,
                    )
                elif point == "t6_event":
                    await ReturnFreightService(writer).resubmit(
                        fid, FreightResubmitRequest(note="修改金额", amount="35"), user
                    )
                else:
                    await ReturnFreightService(writer).pay(
                        fid,
                        filename="pay.png",
                        mime_type="image/png",
                        data=storage_fixtures._PNG,
                        user=user,
                    )
            finally:
                # 与真实get_session异常边界相同；提交过的写入无法靠这里伪造回滚。
                await writer.rollback()
    assert await state(maker, pid) == before, "FLOW8_FAULT_EXPECTED:transaction_atomic"
    if point == "t4_event":
        assert len(r2.puts) == len(r2.deletes) == 1
