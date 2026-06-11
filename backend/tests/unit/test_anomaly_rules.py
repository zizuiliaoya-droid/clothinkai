"""U15 单元测试：异常预警阈值判定纯逻辑（AnomalyAlertService._evaluate_row）。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.modules.wecom.anomaly_service import AnomalyAlertService


def _row(*, return_rate=None, net_roi=None):
    return SimpleNamespace(
        style_id=uuid4(),
        style_code="ST001",
        style_name="测试款",
        return_rate=return_rate,
        net_roi=net_roi,
    )


def _cfg(*, return_rate_threshold="0.4000", low_roi_threshold=None):
    return SimpleNamespace(
        return_rate_threshold=Decimal(return_rate_threshold),
        low_roi_threshold=(
            Decimal(low_roi_threshold) if low_roi_threshold is not None else None
        ),
    )


class TestReturnRateRule:
    def test_above_threshold_fires(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(return_rate=Decimal("0.5000")), _cfg()
        )
        assert [t for t, _ in out] == ["return_rate_high"]

    def test_equal_threshold_no_fire(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(return_rate=Decimal("0.4000")), _cfg()
        )
        assert out == []

    def test_none_no_fire(self) -> None:
        out = AnomalyAlertService._evaluate_row(_row(return_rate=None), _cfg())
        assert out == []


class TestRoiRule:
    def test_below_threshold_fires(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(net_roi=Decimal("0.8000")),
            _cfg(low_roi_threshold="1.0000"),
        )
        assert [t for t, _ in out] == ["roi_low"]

    def test_threshold_null_not_checked(self) -> None:
        # low_roi_threshold=None → 不检（即使 net_roi 很低）
        out = AnomalyAlertService._evaluate_row(
            _row(net_roi=Decimal("0.1000")), _cfg(low_roi_threshold=None)
        )
        assert out == []

    def test_equal_threshold_no_fire(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(net_roi=Decimal("1.0000")),
            _cfg(low_roi_threshold="1.0000"),
        )
        assert out == []


class TestCombined:
    def test_both_fire(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(return_rate=Decimal("0.6000"), net_roi=Decimal("0.5000")),
            _cfg(low_roi_threshold="1.0000"),
        )
        assert sorted(t for t, _ in out) == ["return_rate_high", "roi_low"]

    def test_detail_payload(self) -> None:
        out = AnomalyAlertService._evaluate_row(
            _row(return_rate=Decimal("0.5000")), _cfg()
        )
        _, detail = out[0]
        assert detail["value"] == "0.5000"
        assert detail["threshold"] == "0.4000"
