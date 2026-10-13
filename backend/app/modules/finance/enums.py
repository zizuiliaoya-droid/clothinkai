"""U05 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class SettlementStatus(str, Enum):
    """settlement_status 状态机（5 状态，流程线 PR-6 起去掉核查，设计 3.4）。

    - PENDING_REVIEW: 只剩上线前已建的旧单（F0 核查通过 → 待付款），新单不再进这里
    - PENDING_PAYMENT: 起点（SettlementRequested handler 创建时，F1）；重提也回到这里（F6）
    - PENDING_FINANCE: 填实付金额后（F3）
    - PAID: 财务上传付款凭证后（F4，终态）
    - REJECTED: 待付款 / 待财务付款被驳回（F5），推广单 R7 重提后回到 PENDING_PAYMENT
    """

    PENDING_REVIEW = "待核查"
    PENDING_PAYMENT = "待付款"
    PENDING_FINANCE = "待财务付款"
    PAID = "已付款"
    REJECTED = "已驳回"


class ExtraItemType(str, Enum):
    """SettlementExtraItem.item_type 枚举（3 值）。"""

    SHIPPING = "运费"
    REWARD = "赞奖"
    OTHER = "其他"


class OrderType(str, Enum):
    """U16 order_adjustment.order_type（拍单/刷单统一建模）。"""

    STORE_ORDER = "拍单"
    BRUSHING = "刷单"


class OrderAdjustmentStatus(str, Enum):
    """U16 order_adjustment.status。"""

    PENDING_PAYMENT = "待付款"
    PAID = "已付款"


class BalanceRecordType(str, Enum):
    """U16 balance_record.record_type。"""

    TOPUP = "充值"
    PROMOTION_EXPENSE = "推广支出"
    ORDER_EXPENSE = "刷拍单支出"
    OTHER = "其他"
