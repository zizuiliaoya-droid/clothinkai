"""U10a design 领域逻辑（纯函数，无 ORM/IO）。

- compute_total_cost：自动核价求和（BR-U10a-31）
- compute_available_actions：按状态 + 角色计算可执行动作（detail 用）
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.design.enums import DesignStatus as DS
from app.modules.design.enums import TERMINAL_STATUSES, REJECT_PREVIOUS

# 状态 → (角色 → 可执行动作) 矩阵（available_actions）
_STATUS_ACTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    DS.DESIGNING.value: {"designer": ("submit_fabric",)},
    DS.PATTERNING.value: {
        "pattern_maker": ("submit_pattern", "submit_grading", "reject")
    },
    DS.CRAFTING.value: {"merchandiser": ("submit_craft", "reject")},
    DS.COMPLETING.value: {
        "design_assistant": ("complete_fabric", "submit_costing"),
        "merchandiser": ("reject",),
    },
    DS.PRICING.value: {
        "merchandiser": ("set_tag_price", "confirm_price", "reject")
    },
}


def compute_total_cost(
    fabric_cost: Decimal, accessory_cost: Decimal, craft_cost: Decimal
) -> Decimal:
    """style 级总成本 = 面料 + 辅料 + 工艺费（BR-U10a-31）。"""
    return Decimal(fabric_cost) + Decimal(accessory_cost) + Decimal(craft_cost)


def compute_available_actions(
    design_status: str, role_codes: frozenset[str] | set[str] | list[str]
) -> list[str]:
    """按当前状态 + 角色返回可执行动作集（前端渲染按钮）。"""
    roles = set(role_codes)
    actions: list[str] = []
    role_map = _STATUS_ACTIONS.get(design_status, {})
    is_admin = "admin" in roles or "platform_admin" in roles

    for role, role_actions in role_map.items():
        if role in roles or is_admin:
            for a in role_actions:
                if a not in actions:
                    actions.append(a)

    # admin 可在任意非终态取消
    if is_admin and design_status not in TERMINAL_STATUSES:
        if "cancel" not in actions:
            actions.append("cancel")
    return actions


def can_reject(design_status: str) -> bool:
    """当前状态是否可驳回（存在上一环节）。"""
    return design_status in REJECT_PREVIOUS


__all__ = [
    "can_reject",
    "compute_available_actions",
    "compute_total_cost",
]
