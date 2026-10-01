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


class AmountChangeSource(str, Enum):
    """金额变更来源（金额时间线用）。

    ``_enforce_mode_costs`` 会静默把置换的服务费压成 0、寄拍的样品成本压成 0。
    没有这个标记，PR 看到金额变了会以为是自己改的 —— 而「这个 0 是谁压的」正是
    金额级回溯要回答的问题。
    """

    MANUAL = "手动编辑"
    MODE_INIT = "模式初始化"
    MODE_ENFORCE = "模式兜底"


AMOUNT_LOG_FIELDS: tuple[str, ...] = (
    "quote_amount",
    "cost_snapshot",
    "return_shipping_fee",
)
"""进金额时间线的字段。

``total_promo_cost`` 不记：它是这三项的数据库生成列，回放三项就能推出来，
单独记一行反而可能与分项不一致。
"""


class RetroStatus(str, Enum):
    """复盘状态机（PRD V1.4 改动 4）。

    PRD 原流程最后一步是「已结款 → 发布满 7 天录点赞/收藏/评论+截图 → 已完成」，
    改动 4 在中间插了复盘::

        已结款 → 录 7 天数据(点赞/收藏/评论+截图) → 待复盘
              → PR 手输复盘文字 → 待确认
              → 主管确认 → 已完成（终态）

    **为什么新开一个字段而不是给 settlement_status 加值**：``已付款`` 在
    ``SettlementStatusMachine`` 里是终态，而且全系统有一批查询按
    ``settlement_status = '已付款'`` 过滤（索引、汇总、财务列表）。把「待复盘」
    塞进那个枚举，所有这些查询都会漏掉进入复盘的单子。复盘是正交的一条线，
    独立字段才不互相污染。
    """

    NOT_STARTED = "未开始"
    PENDING_RETRO = "待复盘"
    PENDING_CONFIRM = "待确认"
    COMPLETED = "已完成"


class ReviewAction(str, Enum):
    """PR 主管审核动作（EP05-S13）。"""

    APPROVE = "approve"
    REJECT = "reject"


class RejectReasonCategory(str, Enum):
    """主管驳回原因分类（PRD V1.4 改动 5：三选一必填）。

    自由文本说不清「为什么这单被打回」，分类之后才能统计哪类问题最多、
    以及按原因决定后续动作（衣服未寄回要催寄回，流量差补发要重新排期）。
    """

    LATE_PUBLISH = "延迟发文"
    TRAFFIC_REDO = "流量差补发"
    NOT_RETURNED = "衣服未寄回"


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
