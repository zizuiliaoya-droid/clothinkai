"""U18 单元测试：AI 建议解析 + 生产摘要纯逻辑。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.modules.ai.service import AiAdvisoryService


class TestParseAdvice:
    def test_default_medium(self) -> None:
        r = AiAdvisoryService._parse_advice("建议加大投放", None)
        assert r.confidence == "medium"
        assert r.advice_text == "建议加大投放"
        assert r.data_basis is None

    def test_high_confidence(self) -> None:
        r = AiAdvisoryService._parse_advice("高置信：建议扩量", {"x": 1})
        assert r.confidence == "high"
        assert r.data_basis is not None

    def test_low_confidence(self) -> None:
        r = AiAdvisoryService._parse_advice("低置信：数据不足", None)
        assert r.confidence == "low"


class TestSummarizeProduction:
    def test_summary_aggregates(self) -> None:
        items = [
            SimpleNamespace(style_code="A", pay_amount=Decimal("100"),
                            net_roi=Decimal("2.0")),
            SimpleNamespace(style_code="B", pay_amount=Decimal("50"),
                            net_roi=None),
        ]
        report = SimpleNamespace(items=items)
        s = AiAdvisoryService._summarize_production(report)
        assert s["style_count"] == 2
        assert s["pay_amount_total"] == "150"
        assert len(s["top_styles"]) == 2
        assert s["top_styles"][1]["net_roi"] is None
