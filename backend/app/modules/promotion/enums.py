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


class CooperationMode(str, Enum):
    """合作模式（PRD V1.4 模块二）。单据生成后不可修改。

    三种模式的成本与流转完全不同，是推广单最关键的分支依据：

    - ``寄拍``：衣服要寄回，样品成本恒为 0，只有寄回运费计入成本；
      审核通过后必须先上传博主寄回衣服单号才能流转到待财务付款。
    - ``送拍``：衣服送给博主，样品成本取商品成员款式的货品成本之和；审核通过直接待付款。
    - ``置换``：以货换推广，博主服务费恒为 0；审核通过直接到已结款，不走财务付款。
    """

    CONSIGNMENT = "寄拍"
    GIFT = "送拍"
    BARTER = "置换"
