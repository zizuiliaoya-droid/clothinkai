"""U10a design 模块枚举。"""

from __future__ import annotations

from enum import Enum


class DesignStatus(str, Enum):
    """设计制版状态（7 态）。

    U02 既有取值 `大货` / `设计中` 兼容；其余 5 态 U10a 新增。
    终态：大货、已取消（不可再推进/驳回）。
    """

    DESIGNING = "设计中"
    PATTERNING = "制版中"
    CRAFTING = "工艺录入"
    COMPLETING = "待补全"
    PRICING = "待核价"
    MASS_PRODUCTION = "大货"
    CANCELLED = "已取消"


#: 驳回回退映射（当前状态 → 上一环节）
REJECT_PREVIOUS: dict[str, str] = {
    DesignStatus.PATTERNING.value: DesignStatus.DESIGNING.value,
    DesignStatus.CRAFTING.value: DesignStatus.PATTERNING.value,
    DesignStatus.COMPLETING.value: DesignStatus.CRAFTING.value,
    DesignStatus.PRICING.value: DesignStatus.COMPLETING.value,
}

#: 驳回时 driven_by 角色口径（服务端推断，防伪）
DRIVEN_BY: dict[str, str] = {
    DesignStatus.PATTERNING.value: "version_maker",
    DesignStatus.CRAFTING.value: "merchandiser",
    DesignStatus.COMPLETING.value: "design_assistant",
    DesignStatus.PRICING.value: "merchandiser",
}

#: 推进动作 → 通知的下一环节角色 code
NOTIFY_ROLE: dict[str, str] = {
    "submit_fabric": "pattern_maker",
    "submit_grading": "merchandiser",
    "submit_craft": "design_assistant",
    "submit_costing": "merchandiser",
    "confirm_price": "designer",
}

#: 终态集合
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {DesignStatus.MASS_PRODUCTION.value, DesignStatus.CANCELLED.value}
)


__all__ = [
    "DRIVEN_BY",
    "DesignStatus",
    "NOTIFY_ROLE",
    "REJECT_PREVIOUS",
    "TERMINAL_STATUSES",
]
