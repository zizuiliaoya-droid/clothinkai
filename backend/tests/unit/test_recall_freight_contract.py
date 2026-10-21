"""PR8 请求合同：真实请求解析，先证明旧输入仍允许漏掉召回信息。"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.promotion.schemas import PromotionRecallStartRequest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 2001])
def test_recall_requires_nonblank_reason(reason):
    with pytest.raises(ValidationError):
        PromotionRecallStartRequest(recall_reason=reason)


def test_recall_reason_is_trimmed():
    assert PromotionRecallStartRequest(recall_reason="  博主退出  ").recall_reason == "博主退出"


def test_recall_success_requires_fee_and_payer():
    # 导入放在用例内；缺失类型本身不算业务红灯，API/schema存在后才验金额规则。
    from app.modules.promotion.schemas import PromotionRecallSuccessRequest

    with pytest.raises(ValidationError):
        PromotionRecallSuccessRequest()
    for amount in ("-1", "1.001", "100000000", "NaN"):
        with pytest.raises(ValidationError):
            PromotionRecallSuccessRequest(
                return_shipping_fee=amount, return_shipping_payer="博主垫付"
            )
    accepted = PromotionRecallSuccessRequest(return_shipping_fee="0", return_shipping_payer="到付")
    assert accepted.return_shipping_fee == Decimal("0")


def test_freight_reason_and_amount_contract():
    from app.modules.promotion.freight_schemas import (
        FreightRejectRequest,
        FreightResubmitRequest,
        FreightReviewRequest,
        FreightSubmitRequest,
    )

    for cls, field in ((FreightRejectRequest, "reason"), (FreightResubmitRequest, "note")):
        for value in ("", "   ", "x" * 2001):
            with pytest.raises(ValidationError):
                cls(**{field: value})
        assert getattr(cls(**{field: " x "}), field) == "x"
    with pytest.raises(ValidationError):
        FreightReviewRequest(action="reject", reason=" ")
    assert FreightReviewRequest(action="approve").reason is None
    assert FreightSubmitRequest().amount is None
    for value in ("0", "-1", "1.001", "100000000", "NaN"):
        with pytest.raises(ValidationError):
            FreightSubmitRequest(amount=value)
