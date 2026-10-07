"""8a-7：行失败原因不带 SQL 与参数（runner ``_row_error_detail``，设计 §4.2，N11）。

SQLAlchemy ``StatementError`` / ``DBAPIError`` 的 ``str()`` 自带 ``[SQL: …]`` 与
``[parameters: …]``；ORM 插 SKU 撞唯一索引时参数里就有成本价。失败明细能被看批次的人下载，
所以数据库异常一律换成不带参数的文案。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, StatementError

from app.modules.importer.exceptions import RowValidationError
from app.tasks.import_tasks import _CONCURRENT_UNIQUE, _row_error_detail, _sanitize

_SQL = "INSERT INTO sku (sku_code, cost_price, purchase_price) VALUES ($1, $2, $3)"
_PARAMS = {"sku_code": "K1", "cost_price": Decimal("60.00"), "purchase_price": Decimal("55.50")}

# 驱动层异常的样子（类名就是 asyncpg 里的名字）
UniqueViolationError = type("UniqueViolationError", (Exception,), {})
StringDataRightTruncationError = type("StringDataRightTruncationError", (Exception,), {})
DeadlockDetectedError = type("DeadlockDetectedError", (Exception,), {})


def _assert_clean(text: str) -> None:
    assert "[SQL" not in text
    assert "[parameters" not in text
    assert "60.00" not in text
    assert "55.50" not in text
    assert "INSERT" not in text


@pytest.mark.unit
class TestRowErrorDetail:
    def test_precondition_str_leaks_params(self) -> None:
        """前提：不处理的话 str(exc) 确实带 SQL 与成本价（这条红了说明测试样本不对）。"""
        exc = IntegrityError(_SQL, _PARAMS, UniqueViolationError("dup"))
        assert "60.00" in str(exc)
        assert "60.00" in _sanitize(exc)

    @pytest.mark.parametrize("constraint", _CONCURRENT_UNIQUE)
    def test_concurrent_unique_constraints(self, constraint: str) -> None:
        orig = UniqueViolationError(
            f'duplicate key value violates unique constraint "{constraint}"'
        )
        out = _row_error_detail(IntegrityError(_SQL, _PARAMS, orig))
        assert out == "与另一批次同时写入，请重试"
        _assert_clean(out)

    def test_five_constraints_listed(self) -> None:
        assert set(_CONCURRENT_UNIQUE) == {
            "uq_style_code",
            "uq_sku_code",
            "uq_goods_main_code",
            "uq_blogger_xiaohongshu_id",
            "uq_import_conflict_pending",
        }

    def test_other_integrity_error(self) -> None:
        orig = UniqueViolationError('violates foreign key constraint "fk_sku_style"')
        out = _row_error_detail(IntegrityError(_SQL, _PARAMS, orig))
        assert out == "数据库错误（UniqueViolationError），请重试"
        _assert_clean(out)

    def test_data_error(self) -> None:
        orig = StringDataRightTruncationError("value too long: 60.00")
        out = _row_error_detail(DataError(_SQL, _PARAMS, orig))
        assert out == "数据库错误（StringDataRightTruncationError），请重试"
        _assert_clean(out)

    def test_data_error_without_orig_uses_wrapper_name(self) -> None:
        out = _row_error_detail(DataError(_SQL, _PARAMS, None))  # type: ignore[arg-type]
        assert out == "数据库错误（DataError），请重试"
        _assert_clean(out)

    def test_operational_error(self) -> None:
        orig = DeadlockDetectedError("deadlock detected")
        out = _row_error_detail(OperationalError(_SQL, _PARAMS, orig))
        assert out == "数据库错误（DeadlockDetectedError），请重试"
        _assert_clean(out)

    def test_bare_statement_error(self) -> None:
        """绑定参数处理失败（不是 DBAPIError，但同样带参数）也拦住。"""
        exc = StatementError("bind processing failed", _SQL, _PARAMS, None)
        assert "60.00" in str(exc)
        out = _row_error_detail(exc)
        assert out == "数据库错误（StatementError），请重试"
        _assert_clean(out)

    def test_row_validation_error_unchanged(self) -> None:
        exc = RowValidationError("成本价必须为非负数字; 颜色不能为空")
        assert _row_error_detail(exc) == _sanitize(exc)
        assert "成本价必须为非负数字" in _row_error_detail(exc)

    def test_plain_exception_unchanged(self) -> None:
        exc = ValueError("bad")
        assert _row_error_detail(exc) == _sanitize(exc) == "ValueError: bad"
