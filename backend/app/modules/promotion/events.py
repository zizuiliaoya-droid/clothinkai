"""U04 promotion 模块领域事件。

按 functional-design/domain-entities.md §7 + nfr-design-patterns.md §3 设计：
- ``SettlementRequested``：强一致事件（``required_handler=True``），U05 监听
- ``PromotionPublished``：通知类事件（``required_handler=False``），U07 监听（U04 阶段无 handler）

事件实例不可变（``frozen=True``），通过 ``app.core.events.dispatch(event, session=...)`` 投递。
失败处理见 ``service.py::_log_event_dispatch_failure``（脱敏 audit）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True)
class SettlementRequested:
    """审核 approve 时发出，被 U05 SettlementService 监听并创建 settlement 记录。

    强一致语义（FB1 + FB4）：
    - ``required_handler=True``：U05 未部署时 dispatch 抛 ``MissingRequiredHandlerError``
    - 部署一致性约束：U04 必须 ≥ U05 同批部署（详见 nfr-design §3.3）

    幂等保证：
    - U04 端：每次审核生成新 ``event_id``（UUID4）
    - U05 端：DB ``UNIQUE(promotion_id)`` + service SELECT 兜底
    """

    event_type: ClassVar[str] = "SettlementRequested"
    required_handler: ClassVar[bool] = True

    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal
    pr_id: UUID | None
    requested_by: UUID
    requested_at: datetime


@dataclass(frozen=True)
class PromotionPublished:
    """publish 时发出，预留 U07 企微通知监听。

    通知类语义（FB4）：
    - ``required_handler=False``：无 handler 时仅 warning + 指标，dispatch 不抛错
    - U04 阶段无 listener，事件投递后被丢弃（不阻塞主流程）

    U07 上线后通过 ``subscribe("PromotionPublished", WeworkNotifyService.on_published)``
    注册即可启用。
    """

    event_type: ClassVar[str] = "PromotionPublished"
    required_handler: ClassVar[bool] = False

    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    publish_url: str
    publish_date: date
    pr_id: UUID | None


__all__ = ["PromotionPublished", "SettlementRequested"]
