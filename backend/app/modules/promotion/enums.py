"""U04 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class PublishStatus(str, Enum):
    """publish_status 状态机（5 状态）。"""

    UNPUBLISHED = "未发布"
    PUBLISHED = "已发布"
    CANCELLED = "已取消"
    ABNORMAL = "异常"
    DELETED = "已删除"


class RecallStatus(str, Enum):
    """recall_status 状态机（4 状态）。"""

    NOT_RECALLED = "未召回"
    RECALLING = "召回中"
    RECALLED_SUCCESS = "召回成功"
    RECALLED_FAILURE = "召回失败"


class SettlementStatus(str, Enum):
    """settlement_status 状态机（5 状态）。"""

    NOT_REVIEWED = "未核查"
    PENDING_REVIEW = "待核查"
    PENDING_PAYMENT = "待付款"
    PAID = "已付款"
    REJECTED = "已驳回"


class ReviewAction(str, Enum):
    """PR 主管审核动作（EP05-S13）。"""

    APPROVE = "approve"
    REJECT = "reject"
