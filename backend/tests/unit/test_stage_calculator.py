"""推广单派生阶段（流程线 3.8）：规则顺序、阶段常量与 SQL 渲染的形状。

Python 与 SQL 逐组相等由 ``tests/integration/test_stage_sql_parity.py`` 守。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.modules.promotion.stage_calculator import (
    BY_URGE,
    PROMOTION_STAGES,
    STAGE_COLUMN,
    STAGE_FALLBACK,
    STAGE_RULES,
    URGE_STAGES,
    compute_stage,
    stage_sql_expr,
)
from app.modules.promotion.urge_calculator import UrgeThresholds

TODAY = date(2026, 10, 9)
TH = UrgeThresholds(urge_days=10, important_days=3)


def _stage(**kw: Any) -> str:
    base: dict[str, Any] = {
        "publish_status": "未发布",
        "recall_status": "未召回",
        "settlement_status": "未核查",
        "ship_status": None,
        "is_active": True,
        "scheduled_publish_date": None,
    }
    base.update(kw)
    return compute_stage(**base, today=TODAY, thresholds=TH)


class TestConstants:
    def test_22_stages_unique(self) -> None:
        assert len(PROMOTION_STAGES) == 22
        assert len(set(PROMOTION_STAGES)) == 22

    def test_every_stage_has_column(self) -> None:
        assert set(STAGE_COLUMN) == set(PROMOTION_STAGES)
        assert set(STAGE_COLUMN.values()) == set("ABCDEFGH")

    def test_columns_follow_design(self) -> None:
        assert [s for s in PROMOTION_STAGES if STAGE_COLUMN[s] == "C"] == list(URGE_STAGES)
        assert STAGE_COLUMN["待推送仓库"] == "A"
        assert STAGE_COLUMN["待仓库发货"] == "B"
        assert STAGE_COLUMN["召回中"] == "G"
        assert STAGE_COLUMN["已停用"] == "H"
        assert STAGE_COLUMN[STAGE_FALLBACK] == "H"

    def test_rule_names_are_registered_stages(self) -> None:
        for rule in STAGE_RULES:
            assert rule.name == BY_URGE or rule.name in PROMOTION_STAGES, rule.name
        names = [r.name for r in STAGE_RULES]
        assert len(set(names)) == len(names)


class TestOrder:
    def test_deleted_beats_inactive(self) -> None:
        assert _stage(publish_status="已删除", is_active=False) == "已删除"

    def test_inactive_beats_everything_else(self) -> None:
        assert _stage(is_active=False, recall_status="召回中", ship_status="待发货") == "已停用"

    def test_cancelled_recalled_is_recall_closed(self) -> None:
        assert _stage(publish_status="已取消", recall_status="召回成功") == "已完结 · 召回"

    def test_recalling(self) -> None:
        assert _stage(publish_status="已发布", recall_status="召回中") == "召回中"
        assert _stage(publish_status="已取消", recall_status="召回中") == "召回中"

    @pytest.mark.parametrize("recall", ["未召回", "召回失败"])
    def test_cancelled_not_cooperating(self, recall: str) -> None:
        assert _stage(publish_status="已取消", recall_status=recall) == "已完结 · 不合作"

    @pytest.mark.parametrize("publish", ["未发布", "异常"])
    def test_ship_stages_before_urge(self, publish: str) -> None:
        sched = TODAY - timedelta(days=5)
        assert _stage(
            publish_status=publish, ship_status="待发货", scheduled_publish_date=sched
        ) == ("待推送仓库")
        assert _stage(
            publish_status=publish, ship_status="待打单", scheduled_publish_date=sched
        ) == ("待仓库发货")

    @pytest.mark.parametrize(
        ("sched", "expected"),
        [
            (None, "未排期"),
            (TODAY + timedelta(days=11), "档期内"),
            (TODAY + timedelta(days=10), "催发"),
            (TODAY + timedelta(days=3), "重要催发"),
            (TODAY, "重要催发"),
            (TODAY - timedelta(days=1), "超时"),
        ],
    )
    @pytest.mark.parametrize("ship", [None, "已发货"])
    def test_unpublished_takes_urge(
        self, sched: date | None, expected: str, ship: str | None
    ) -> None:
        assert _stage(ship_status=ship, scheduled_publish_date=sched) == expected

    def test_published_ship_status_ignored(self) -> None:
        assert _stage(
            publish_status="已发布", ship_status="待发货", settlement_status="待核查"
        ) == ("待主管审核")

    @pytest.mark.parametrize(
        ("settlement", "expected"),
        [
            ("待核查", "待主管审核"),
            ("已驳回", "推广驳回 · 待重提"),
            ("待付款", "待财务付款"),
            ("已付款", "待复盘"),
            ("未核查", STAGE_FALLBACK),
        ],
    )
    def test_published_by_settlement(self, settlement: str, expected: str) -> None:
        assert _stage(publish_status="已发布", settlement_status=settlement) == expected

    def test_published_recall_failed_follows_settlement(self) -> None:
        assert _stage(
            publish_status="已发布", recall_status="召回失败", settlement_status="待付款"
        ) == ("待财务付款")


class TestSqlShape:
    def test_case_has_one_when_per_rule_and_fallback(self) -> None:
        sql = stage_sql_expr()
        assert sql.startswith("CASE")
        assert sql.rstrip().endswith("END")
        # 第 8 条嵌的催发 CASE 自带 WHEN；只数顶层（两个空格缩进）的 WHEN
        top = [ln for ln in sql.splitlines() if ln.startswith("  WHEN ")]
        assert len(top) == len(STAGE_RULES)
        assert f"ELSE '{STAGE_FALLBACK}'" in sql

    def test_no_current_date(self) -> None:
        assert "CURRENT_DATE" not in stage_sql_expr().upper()
