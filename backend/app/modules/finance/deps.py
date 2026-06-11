"""U05 finance 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.finance.balance_service import BalanceService
from app.modules.finance.order_adjustment_service import OrderAdjustmentService
from app.modules.finance.service import SettlementService


def get_settlement_service(session: SessionDep) -> SettlementService:
    return SettlementService(session)


def get_order_adjustment_service(session: SessionDep) -> OrderAdjustmentService:
    return OrderAdjustmentService(session)


def get_balance_service(session: SessionDep) -> BalanceService:
    return BalanceService(session)


SettlementServiceDep = Annotated[
    SettlementService, Depends(get_settlement_service)
]
OrderAdjustmentServiceDep = Annotated[
    OrderAdjustmentService, Depends(get_order_adjustment_service)
]
BalanceServiceDep = Annotated[BalanceService, Depends(get_balance_service)]


__all__ = [
    "BalanceServiceDep",
    "OrderAdjustmentServiceDep",
    "SettlementServiceDep",
    "get_balance_service",
    "get_order_adjustment_service",
    "get_settlement_service",
]
