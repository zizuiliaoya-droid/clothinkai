"""复盘状态机规则（PRD V1.4 改动 4）。纯规则，不碰数据库。

放在 ``tests/unit/`` 而不是跟集成测试一个文件：那边的模块级 ``pytestmark`` 带
``pytest.mark.asyncio``，会把同步测试一起标上然后每条报一个 warning。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import IllegalStateTransitionError
from app.modules.promotion.enums import RetroStatus
from app.modules.promotion.state_machines import (
    RetroStatusMachine,
    SettlementStatusMachine,
)

pytestmark = [pytest.mark.unit]


class TestLegalTransitions:
    @pytest.mark.parametrize(
        ("from_state", "action", "to_state"),
        [
            ("未开始", "record_metrics", "待复盘"),
            ("待复盘", "submit_retro", "待确认"),
            ("待确认", "confirm_retro", "已完成"),
            # 打回不在 PRD 原文里。没有它主管看完觉得写得没用时只剩「卡死」
            # 或「硬着头皮确认」两条路
            ("待确认", "reject_retro", "待复盘"),
        ],
    )
    def test_allowed(self, from_state: str, action: str, to_state: str) -> None:
        RetroStatusMachine.assert_can_transition(
            from_state=from_state, to_state=to_state, action=action
        )


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        ("from_state", "action", "to_state"),
        [
            # 跳过复盘直接完成
            ("待复盘", "confirm_retro", "已完成"),
            # 没录 7 天数据就想写复盘
            ("未开始", "submit_retro", "待确认"),
            # 已完成是终态
            ("已完成", "reject_retro", "待复盘"),
            ("已完成", "record_metrics", "待复盘"),
            # 录数据不能跳到待确认
            ("未开始", "record_metrics", "待确认"),
        ],
    )
    def test_rejected(self, from_state: str, action: str, to_state: str) -> None:
        with pytest.raises(IllegalStateTransitionError):
            RetroStatusMachine.assert_can_transition(
                from_state=from_state, to_state=to_state, action=action
            )

    def test_completed_is_terminal(self) -> None:
        assert RetroStatusMachine.get_allowed_transitions(RetroStatus.COMPLETED.value) == []


class TestOrthogonality:
    """复盘与结款是两条独立的线，这是本批最关键的设计决定。

    如果有人把「待复盘」塞进 ``SettlementStatusMachine``，下面两条会红 ——
    那样做会让所有按 ``settlement_status='已付款'`` 过滤的查询（索引、汇总、
    财务列表）漏掉进入复盘的单子。
    """

    def test_settlement_paid_still_terminal(self) -> None:
        assert SettlementStatusMachine.get_allowed_transitions("已付款") == []

    def test_retro_states_not_in_settlement_machine(self) -> None:
        settlement_states = {t.to_state for t in SettlementStatusMachine.transitions} | {
            t.from_state for t in SettlementStatusMachine.transitions
        }
        retro_states = {s.value for s in RetroStatus}
        assert settlement_states & retro_states == set()
