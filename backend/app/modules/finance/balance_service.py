"""U16 BalanceService（余额流水自动计算 + 一致性/类型字段校验）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.finance.enums import BalanceRecordType
from app.modules.finance.exceptions import (
    BalanceMismatchError,
    BalanceTypeFieldMismatchError,
)
from app.modules.finance.order_adjustment_models import BalanceRecord
from app.modules.finance.order_adjustment_repository import (
    BalanceRecordRepository,
)
from app.modules.finance.order_adjustment_schemas import BalanceRecordCreate

_EXPENSE_TYPES = {
    BalanceRecordType.PROMOTION_EXPENSE.value,
    BalanceRecordType.ORDER_EXPENSE.value,
}


class BalanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BalanceRecordRepository(session)

    async def add_record(
        self, payload: BalanceRecordCreate, user: Any
    ) -> BalanceRecord:
        self._validate_type_field(payload)
        prev = await self._repo.last_balance(user.tenant_id)
        income = payload.income or Decimal("0")
        expense = payload.expense or Decimal("0")
        balance_after = prev + income - expense
        if (
            payload.expected_balance is not None
            and payload.expected_balance != balance_after
        ):
            raise BalanceMismatchError(
                f"余额不一致：计算={balance_after} 填写={payload.expected_balance}"
            )
        row = BalanceRecord(
            record_date=payload.record_date,
            record_type=payload.record_type,
            income=payload.income,
            expense=payload.expense,
            balance_after=balance_after,
            remark=payload.remark,
            created_by=user.id,
        )
        self._repo.add(row)
        await self._session.flush()
        await AuditService(self._session).log(
            "finance.balance.add",
            resource="balance_record",
            resource_id=row.id,
            user_id=user.id,
        )
        await self._session.commit()
        return row

    @staticmethod
    def _validate_type_field(p: BalanceRecordCreate) -> None:
        if p.record_type == BalanceRecordType.TOPUP.value:
            if not p.income or p.income <= 0 or p.expense is not None:
                raise BalanceTypeFieldMismatchError("充值仅可填收入(income)")
        elif p.record_type in _EXPENSE_TYPES:
            if not p.expense or p.expense <= 0 or p.income is not None:
                raise BalanceTypeFieldMismatchError("支出类仅可填支出(expense)")
        else:  # 其他：且仅填一项 > 0
            if bool(p.income) == bool(p.expense):
                raise BalanceTypeFieldMismatchError(
                    "income/expense 须且仅填一项"
                )

    async def list(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        return await self._repo.list(
            date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )


__all__ = ["BalanceService"]
