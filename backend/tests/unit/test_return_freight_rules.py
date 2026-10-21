from itertools import product

import pytest

from app.modules.promotion.freight_rules import FreightStateConflictError, freight_transition

pytestmark = pytest.mark.unit

EXPECTED = {
    ("待审核", "approve"): "待付款",
    ("待审核", "reject"): "已驳回",
    ("待付款", "pay"): "已付款",
    ("待付款", "reject_payment"): "已驳回",
    ("已驳回", "resubmit"): "待审核",
}


@pytest.mark.parametrize(
    "status,action",
    list(
        product(
            ("待审核", "待付款", "已付款", "已驳回"),
            ("approve", "reject", "pay", "reject_payment", "resubmit"),
        )
    ),
)
def test_freight_transition_matrix(status, action):
    if (status, action) in EXPECTED:
        assert freight_transition(status, action) == EXPECTED[status, action]
    else:
        with pytest.raises(FreightStateConflictError) as exc:
            freight_transition(status, action)
        assert exc.value.status_code == 409
        assert exc.value.code == "FREIGHT_STATE_CONFLICT"


def test_freight_table_maps_existing_snapshot_and_no_delete_fields():
    from sqlalchemy.orm import configure_mappers

    from app.modules.promotion.freight_models import ReturnFreightPayment

    configure_mappers()
    table = ReturnFreightPayment.__table__
    assert table.c.amount.type.precision == 10 and table.c.amount.type.scale == 2
    assert not table.c.payment_qr_attachment_id.nullable
    assert not table.c.waybill_attachment_id.nullable
    assert "is_active" not in table.c
    assert any(i.name == "uq_return_freight_promotion" and i.unique for i in table.indexes)
