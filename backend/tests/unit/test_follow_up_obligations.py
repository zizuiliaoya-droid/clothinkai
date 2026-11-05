"""独评触发条件的SQL三值逻辑回归；真实PG列表/汇总另由集成测试验证。"""

import sqlite3

import pytest

from app.modules.flow.engagement import follow_up_open_sql

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "status,ship,mode,waybill,recall,expected",
    [
        ("已取消", None, None, None, "未召回", False),
        ("已取消", None, "寄拍", None, "未召回", False),
        ("未发布", "已发货", "寄拍", None, "未召回", True),
        ("未发布", "已发货", "寄拍", "RETURN-1105", "未召回", False),
        ("未发布", "已发货", "寄拍", None, "召回成功", False),
    ],
)
def test_remaining_obligation_is_boolean_and_keeps_loaned_sample(
    status,
    ship,
    mode,
    waybill,
    recall,
    expected,
):
    with sqlite3.connect(":memory:") as conn:
        conn.create_function("BTRIM", 1, lambda value: value.strip() if value is not None else None)
        conn.execute("""CREATE TABLE promotion(id,tenant_id,publish_status,ship_status,
            cooperation_mode,return_waybill,recall_status,is_active,completed_at)""")
        conn.execute("CREATE TABLE settlement(tenant_id,promotion_id,settlement_status)")
        conn.execute("CREATE TABLE return_freight_payment(tenant_id,promotion_id,status)")
        conn.execute(
            "INSERT INTO promotion VALUES(1,1,?,?,?,?,?,0,NULL)",
            (status, ship, mode, waybill, recall),
        )
        value = conn.execute(
            f"SELECT {follow_up_open_sql('p','promotion')} FROM promotion p"
        ).fetchone()[0]
    assert value is not None
    assert bool(value) is expected
