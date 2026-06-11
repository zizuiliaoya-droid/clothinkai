"""U05 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class SettlementStatus(str, Enum):
    """settlement_status 状态机（5 状态，FB1）。

    - PENDING_REVIEW: 起点（SettlementRequested handler 创建时）
    - PENDING_PAYMENT: PR 主管 approve 后
    - PENDING_FINANCE: PR 主管 fill_payment 后
    - PAID: 财务 mark_paid 后（终态）
    - REJECTED: PR 主管 reject 后（可 resubmit 回到 PENDING_REVIEW）
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
