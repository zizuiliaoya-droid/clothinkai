"""U05 finance 模块领域事件。

按 functional-design/domain-entities.md §7 + nfr-design-patterns.md §5 设计：
- ``SettlementPaid``：U05 → U04 反向通知类事件（``required_handler=False``，FB5）
- ``SettlementRejected``：finance → promotion 强一致事件（``required_handler=True``，流程线 R6）

事件实例不可变（``frozen=True``），通过 ``app.core.events.dispatch(event, session=...)`` 投递。
失败处理见 ``service.py::_log_event_dispatch_failure``（脱敏 audit + blocking=False 不阻塞主流程）。

与 U04 SettlementRequested（required_handler=True 强一致）形成不对称：
- SettlementRequested 失败 → service 层 raise → 事务回滚
- SettlementPaid 失败 → service 层 try/except → log + 指标 + 不阻塞 commit
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True)
class SettlementPaid:
    """U05 → U04 反向通知：mark_paid 时同步 promotion.settlement_status='已付款'.

    通知类语义（FB5）：
    - ``required_handler=False``：U04 端 listener 缺失不影响 U05 主流程
    - U04 端 listener 监听 → UPDATE promotion SET settlement_status='已付款' WHERE 旧状态='待付款'
    - V1 引入 reconcile Celery beat 任务每天凌晨 03:00 兜底同步

    与 SettlementRequested（required_handler=True）形成不对称：
    - 部署一致性：U04 listener 缺失不阻塞 U05 部署
    - 失败处理：service 层 try/except + 不重新 raise
    """

    event_type: ClassVar[str] = "SettlementPaid"
    required_handler: ClassVar[bool] = False  # 通知类，与 SettlementRequested 不对称

    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    payment_amount: Decimal
    payment_date: date
    paid_by: UUID


@dataclass(frozen=True)
class SettlementRejected:
    """财务驳回结款单（F5）→ 推广单「结款驳回」（R6，流程线 3.4、7.5）。

    强一致（与 ``SettlementPaid`` 不同）：
    - ``required_handler=True``：promotion 监听没注册时 dispatch 抛 ``MissingRequiredHandlerError``，
      不能让推广单与结款单状态分叉
    - ``SettlementService.review`` 在结款单条件 UPDATE 之后、commit 之前同一 session 发出；
      监听做推广单的条件 UPDATE（0 行 → 409）并写 ``settlement.rejected``，不 commit、不吞异常
    """

    event_type: ClassVar[str] = "SettlementRejected"
    required_handler: ClassVar[bool] = True

    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    reason: str
    actor_id: UUID
    from_state: str
    """结款单驳回前的状态（待付款 / 待财务付款）：``settlement.rejected`` 的 from_state 记结款单这一维。"""


__all__ = ["SettlementPaid", "SettlementRejected"]
