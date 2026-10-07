"""商品显示名：有简称用简称，没填回落全称。

全称动辄二三十个字（「LENNEA芭蕾风假两件蕾丝花边拼接阔腿裤女设计感高腰直筒休闲裤」），
业务在商品页另填一个简称；还没填的商品不能因此显示成空白。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.product.goods_schemas import goods_display_name
from app.modules.wecom.anomaly_service import AnomalyAlertService
from app.modules.wecom.enums import AlertType

FULL = "LENNEA芭蕾风假两件蕾丝花边拼接阔腿裤女设计感高腰直筒休闲裤"


class TestGoodsDisplayName:
    def test_short_name_wins(self) -> None:
        assert goods_display_name(FULL, "芭蕾风阔腿裤") == "芭蕾风阔腿裤"

    def test_falls_back_to_full_title(self) -> None:
        assert goods_display_name(FULL, None) == FULL


class TestAnomalyAlertGoodsLine:
    @staticmethod
    def _goods_line(short_name: str | None) -> str:
        row = SimpleNamespace(
            goods_code="240627",
            goods_title=FULL,
            goods_short_name=short_name,
            is_suit=False,
            style_codes=["240627"],
        )
        text = AnomalyAlertService._render(
            AlertType.RETURN_RATE_HIGH.value, row, {"value": "0.5600", "threshold": "0.4000"}
        )
        return next(ln for ln in text.splitlines() if ln.startswith("> 商品："))

    # 补充 2：消息里不再带商品编码（原断言「> 商品：240627 芭蕾风阔腿裤」）
    def test_uses_short_name(self) -> None:
        assert self._goods_line("芭蕾风阔腿裤") == "> 商品：芭蕾风阔腿裤"

    def test_without_short_name_shows_full_title(self) -> None:
        assert self._goods_line(None) == f"> 商品：{FULL}"
