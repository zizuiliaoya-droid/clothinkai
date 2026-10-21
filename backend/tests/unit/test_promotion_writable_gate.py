"""统一锁内门禁保留PR6停用竞争409，初始不可见仍404；不模拟真实DB锁验证。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.modules.promotion.models import Promotion
from app.modules.promotion.service_parts.base import PromotionServiceBase

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "state,star,include_inactive,expected",
    [
        ("deactivated_while_waiting", False, False, (409, "PROMOTION_STATE_CONFLICT")),
        ("initially_inactive", False, False, (404, "PROMOTION_NOT_FOUND")),
        ("missing", False, False, (404, "PROMOTION_NOT_FOUND")),
        ("completed", False, False, (409, "PROMOTION_COMPLETED_READONLY")),
        ("completed", True, False, None),
        ("initially_inactive", False, True, None),
    ],
)
async def test_writable_gate_distinguishes_deactivation_race_from_initial_state(
    state, star, include_inactive, expected
) -> None:
    promotion = (
        None
        if state == "missing"
        else Promotion(
            id=uuid4(),
            is_active=state != "initially_inactive",
            completed_at=datetime.now(UTC) if state == "completed" else None,
        )
    )
    service = PromotionServiceBase.__new__(PromotionServiceBase)

    async def locked(*args, **kwargs):
        # populate_existing会原地刷新identity-map实体；务必在等待前复制布尔值。
        if state == "deactivated_while_waiting":
            promotion.is_active = False
        return promotion

    service._repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=promotion), get_for_write=AsyncMock(side_effect=locked)
    )
    service._flow_actor = AsyncMock(return_value=SimpleNamespace(is_star=star))
    user = SimpleNamespace(tenant_id=uuid4())
    if expected:
        with pytest.raises(AppException) as error:
            await service._writable_promotion(uuid4(), user, include_inactive=include_inactive)
        assert (error.value.status_code, error.value.code) == expected
    else:
        assert (
            await service._writable_promotion(uuid4(), user, include_inactive=include_inactive)
            is promotion
        )
