"""真实QR门禁的本地纯业务测试；session隔离，不声称数据库权限证据。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.promotion.exceptions import PaymentQrLockedError
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def service_and_promotion(*, recall="召回中", completed=False, freight=None, paying=False):
    session = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = freight
    session.execute.return_value = result
    service = PromotionService(session)
    service._flow_actor = AsyncMock(return_value=SimpleNamespace(is_star=False))
    service._repo.settlement_brief = AsyncMock(
        return_value=SimpleNamespace(settlement_status="待财务付款") if paying else None
    )
    service._compute_stage = AsyncMock(return_value="已完结" if completed else "召回中")
    promotion = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        publish_status="已发布",
        recall_status=recall,
        settlement_status="已结款",
        is_active=True,
        completed_at=datetime.now(UTC) if completed else None,
    )
    return service, promotion


@pytest.mark.parametrize("completed", [False, True])
async def test_recalling_settled_qr_can_change(completed):
    service, promotion = service_and_promotion(completed=completed)
    assert await service._assert_payment_qr_changeable(promotion, SimpleNamespace()) is None


@pytest.mark.parametrize("completed", [False, True])
async def test_rejected_freight_settled_qr_can_change(completed):
    service, promotion = service_and_promotion(
        recall="召回成功", completed=completed, freight="已驳回"
    )
    promotion.publish_status = "已取消" if completed else "已发布"
    assert await service._assert_payment_qr_changeable(promotion, SimpleNamespace()) is None


async def test_freight_exception_never_bypasses_finance_payment_lock():
    service, promotion = service_and_promotion(completed=True, freight="已驳回", paying=True)
    with pytest.raises(PaymentQrLockedError) as exc:
        await service._assert_payment_qr_changeable(promotion, SimpleNamespace())
    assert exc.value.details["lock"] == "settlement_paying"
