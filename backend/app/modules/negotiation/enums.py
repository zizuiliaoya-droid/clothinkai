"""谈款审核枚举。"""

from __future__ import annotations

from enum import Enum


class NegotiationStatus(str, Enum):
    """谈款单状态（PRD V1.4 模块一）。

    流转：草稿 --提交--> 待审核 --主管审核--> 审核通过（终态，已生成推广单）
                                      \\--> 审核驳回 --PR 修改--> 草稿

    「审核通过」是终态：推广单已经建出来了，再改谈款信息不会同步过去，
    所以不允许回退。要改就去改推广单本身。
    """

    DRAFT = "草稿"
    PENDING = "待审核"
    APPROVED = "审核通过"
    REJECTED = "审核驳回"


class NegotiationReviewAction(str, Enum):
    """主管审核动作。"""

    APPROVE = "approve"
    REJECT = "reject"


__all__ = ["NegotiationReviewAction", "NegotiationStatus"]
