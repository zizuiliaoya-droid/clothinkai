"""U10a DesignStateMachine 转移表（复用 core/state_machine）。

P-U10a-01：状态机做语义校验（find rule + actor_roles + required_fields）；
实际状态变更由 repository.update_design_status 乐观并发 UPDATE 完成。
原地动作（submit_pattern / complete_fabric / set_tag_price）不在转移表中。
reject / cancel 为动态目标（见 enums.REJECT_PREVIOUS），单独处理。
"""

from __future__ import annotations

from app.core.state_machine import StateMachine, TransitionRule
from app.modules.design.enums import DesignStatus as DS

DESIGN_TRANSITIONS: tuple[TransitionRule, ...] = (
    TransitionRule(
        from_state=DS.DESIGNING.value,
        action="submit_fabric",
        to_state=DS.PATTERNING.value,
        actor_roles=("designer", "admin"),
        required_fields=("fabrics",),
        side_effects=("upsert_fabric", "notify_pattern_maker"),
    ),
    TransitionRule(
        from_state=DS.PATTERNING.value,
        action="submit_grading",
        to_state=DS.CRAFTING.value,
        actor_roles=("pattern_maker", "admin"),
        side_effects=("require_pattern", "notify_merchandiser"),
    ),
    TransitionRule(
        from_state=DS.CRAFTING.value,
        action="submit_craft",
        to_state=DS.COMPLETING.value,
        actor_roles=("merchandiser", "admin"),
        required_fields=("craft_info",),
        side_effects=("upsert_craft", "notify_design_assistant"),
    ),
    TransitionRule(
        from_state=DS.COMPLETING.value,
        action="submit_costing",
        to_state=DS.PRICING.value,
        actor_roles=("design_assistant", "admin"),
        required_fields=("cost_breakdown",),
        side_effects=("auto_costing", "notify_merchandiser"),
    ),
    TransitionRule(
        from_state=DS.PRICING.value,
        action="confirm_price",
        to_state=DS.MASS_PRODUCTION.value,
        actor_roles=("merchandiser", "admin"),
        side_effects=("notify_designer_done",),
    ),
)


def make_design_state_machine(style: object) -> StateMachine:
    """构造作用于 style.design_status 的状态机。"""
    return StateMachine(
        target=style,
        transition_table=DESIGN_TRANSITIONS,
        state_attr="design_status",
    )


__all__ = ["DESIGN_TRANSITIONS", "make_design_state_machine"]
