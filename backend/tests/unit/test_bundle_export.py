"""U17 单元测试：导出 _cell 序列化纯逻辑。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.report.export_service import _cell


class TestCellSerialization:
    def test_none_to_empty(self) -> None:
        assert _cell(None) == ""

    def test_decimal_to_str(self) -> None:
        assert _cell(Decimal("12.34")) == "12.34"

    def test_int_passthrough(self) -> None:
        assert _cell(100) == 100

    def test_str_passthrough(self) -> None:
        assert _cell("ST001") == "ST001"

    def test_date_passthrough(self) -> None:
        d = date(2026, 6, 1)
        assert _cell(d) == d
