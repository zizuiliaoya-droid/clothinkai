"""谈款审核枚举。"""

from __future__ import annotations

from enum import Enum


class NegotiationStatus(str, Enum):
    """谈款单状态（迁移 063 起 7 态两级审核；迁移 077 新增「已取消」）。

    流转：草稿 --提交--> 主管待审 --主管通过--> 待老板审核 --老板定稿--> 待补收货地址 --确认收货--> 已定稿
                            \\--主管驳回--> 审核驳回 --原单重提--> 主管待审；满 7 天未重提 → 已取消
                                                  \\--老板退回--> 老板退回 --PR 改完重提--> 主管待审

    「已定稿」是终态：推广单已经建出来了，再改谈款信息不会同步过去。
    ``PENDING`` / ``APPROVED`` 沿用旧成员名（旧「待审核」= 主管待审、旧「审核通过」= 已定稿，迁移里同义改名）。
    """

    DRAFT = "草稿"
    PENDING = "主管待审"
    REJECTED = "审核驳回"
    FINAL_PENDING = "待老板审核"
    FINAL_RETURNED = "老板退回"
    RECEIVER_PENDING = "待补收货地址"
    APPROVED = "已定稿"
    CANCELLED = "已取消"


class NegotiationReviewAction(str, Enum):
    """主管审核动作。"""

    APPROVE = "approve"
    REJECT = "reject"


class NegotiationFinalReviewAction(str, Enum):
    """老板审核动作（N6 定稿 / N7 退回）。"""

    APPROVE = "approve"
    RETURN = "return"


__all__ = ["NegotiationFinalReviewAction", "NegotiationReviewAction", "NegotiationStatus"]
