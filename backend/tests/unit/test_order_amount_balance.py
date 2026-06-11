"""U16 单元测试：金额表达式解析 + 余额类型字段校验纯逻辑。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.finance.exceptions import (
    AmountExpressionInvalidError,
    BalanceTypeFieldMismatchError,
)
from app.modules.finance.balance_service import BalanceService
from app.modules.finance.order_adjustment_schemas import BalanceRecordCreate
from app.modules.finance.order_adjustment_service import parse_amount_expr


class TestParseAmountExpr:
    def test_plain_number(self) -> None:
        assert parse_amount_expr("100") == Decimal("100")

    def test_decimal(self) -> None:
        assert parse_amount_expr("99.90") == Decimal("99.90")

    def test_expression(self) -> None:
        assert parse_amount_expr("100-30") == Decimal("70")

    def test_expression_with_spaces(self) -> None:
        assert parse_amount_expr(" 100 - 30 ") == Decimal("70")

    def test_decimal_passthrough(self) -> None:
        assert parse_amount_expr(Decimal("70")) == Decimal("70")

    def test_multiple_operators_invalid(self) -> None:
        with pytest.raises(AmountExpressionInvalidError):
            parse_amount_expr("100-30-5")

    def test_non_numeric_invalid(self) -> None:
        with pytest.raises(AmountExpressionInvalidError):
            parse_amount_expr("abc")

    def test_negative_result_invalid(self) -> None:
        with pytest.raises(AmountExpressionInvalidError):
            parse_amount_expr("30-100")


def _payload(record_type, *, income=None, expense=None):
    return BalanceRecordCreate(
        record_date=__import__("datetime").date(2026, 6, 1),
        record_type=record_type, income=income, expense=expense,
    )


class TestBalanceTypeFieldValidation:
    def test_topup_requires_income(self) -> None:
        BalanceService._validate_type_field(
            _payload("充值", income=Decimal("100"))
        )  # 不抛

    def test_topup_with_expense_invalid(self) -> None:
        with pytest.raises(BalanceTypeFieldMismatchError):
            BalanceService._validate_type_field(
                _payload("充值", income=Decimal("100"), expense=Decimal("10"))
            )

    def test_expense_type_requires_expense(self) -> None:
        BalanceService._validate_type_field(
            _payload("推广支出", expense=Decimal("50"))
        )

    def test_expense_type_with_income_invalid(self) -> None:
        with pytest.raises(BalanceTypeFieldMismatchError):
            BalanceService._validate_type_field(
                _payload("刷拍单支出", income=Decimal("50"))
            )

    def test_topup_no_income_invalid(self) -> None:
        with pytest.raises(BalanceTypeFieldMismatchError):
            BalanceService._validate_type_field(_payload("充值"))
