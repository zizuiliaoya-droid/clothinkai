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
    """settlement_status 状态机（7 状态，流程线 3.4；列 String(16) 无 CHECK）。

    界面文案：未核查 = 未发布、待核查 = 待主管审核、已驳回 = 推广驳回 · 待重提、
    待付款 = 待财务付款、结款驳回 = 结款驳回 · 待重提、已付款 = 待传结款截图、已结款 = 已结款。
    """

    NOT_REVIEWED = "未核查"
    PENDING_REVIEW = "待核查"
    PENDING_PAYMENT = "待付款"
    PAID = "已付款"
    REJECTED = "已驳回"
    SETTLEMENT_REJECTED = "结款驳回"
    SETTLED = "已结款"


class ShipStatus(str, Enum):
    """ship_status 发货 3 态（流程线 3.3）。库里 NULL = 历史单，没进系统的发货流程。

    待发货 → 待打单（主管 / 管理员确认推送仓库）→ 已发货（仓库回填快递信息）；
    待打单可撤回或随取消退回待发货。
    """

    PENDING = "待发货"
    PRINTING = "待打单"
    SHIPPED = "已发货"


class ShipCourier(str, Enum):
    """快递公司（11-25：10 个）。库里不加 CHECK，增减只改这里。"""

    SF = "顺丰"
    ZTO = "中通"
    YTO = "圆通"
    YUNDA = "韵达"
    STO = "申通"
    JT = "极兔"
    EMS = "邮政"
    JD = "京东"
    DEPPON = "德邦"
    OTHER = "其他"


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
    """复盘三态（流程线3.6）：发布进入待复盘，主管写完即完成。"""

    NOT_STARTED = "未开始"
    PENDING_RETRO = "待复盘"
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
