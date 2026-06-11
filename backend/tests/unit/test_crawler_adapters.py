"""U13 单元测试：3 adapter parse/validate + worker token hash。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.collect.worker_token_service import hash_token
from app.modules.importer.adapters.huitun import HuitunImportAdapter
from app.modules.importer.adapters.qianniu import QianniuImportAdapter
from app.modules.importer.adapters.wanxiangtai import WanxiangtaiImportAdapter


class TestQianniuAdapter:
    def test_parse_and_validate_ok(self) -> None:
        a = QianniuImportAdapter()
        parsed = a.parse_row(
            {
                "商品ID": "123456",
                "日期": "2026-06-08",
                "访客数": "1,200",
                "支付金额": "3500.50",
                "支付订单数": "42",
            },
            None,
        )
        assert parsed["platform_id"] == "123456"
        assert parsed["date"] == date(2026, 6, 8)
        assert parsed["visitors"] == 1200
        assert parsed["pay_amount"] == Decimal("3500.50")
        assert a.validate(parsed) == []

    def test_validate_missing_id(self) -> None:
        a = QianniuImportAdapter()
        parsed = a.parse_row({}, None)
        errs = a.validate(parsed)
        assert any("商品ID" in e for e in errs)
        assert any("日期" in e for e in errs)


class TestWanxiangtaiAdapter:
    def test_parse_ok(self) -> None:
        a = WanxiangtaiImportAdapter()
        parsed = a.parse_row(
            {
                "商品ID": "999",
                "日期": "2026/06/08",
                "花费": "120.00",
                "曝光": "5000",
                "点击": "300",
                "成交额": "2000",
            },
            None,
        )
        assert parsed["platform_id"] == "999"
        assert parsed["date"] == date(2026, 6, 8)
        assert parsed["cost"] == Decimal("120.00")
        assert parsed["clicks"] == 300
        assert a.validate(parsed) == []


class TestHuitunAdapter:
    def test_build_profile(self) -> None:
        a = HuitunImportAdapter()
        parsed = a.parse_row(
            {"小红书ID": "xhs1", "平均点赞": "50", "平均阅读": "1000"}, None
        )
        assert parsed["xiaohongshu_id"] == "xhs1"
        profile = a._build_profile(parsed)
        assert profile["note_stats"]["avg_likes"] == 50
        assert profile["note_stats"]["avg_reads"] == 1000

    def test_validate_missing_id(self) -> None:
        a = HuitunImportAdapter()
        assert a.validate(a.parse_row({}, None)) == ["小红书ID不能为空"]


class TestWorkerTokenHash:
    def test_hash_deterministic(self) -> None:
        assert hash_token("abc") == hash_token("abc")
        assert hash_token("abc") != hash_token("abd")
        assert len(hash_token("abc")) == 64
