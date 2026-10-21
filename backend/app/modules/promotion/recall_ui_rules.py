"""召回专用能力；读取完整状态快照，不按角色名字绕过完结。"""

from dataclasses import dataclass
from typing import Any

from app.modules.flow.matrix import Capability, FlowActor
from app.modules.promotion.freight_rules import continuing_published_recall


@dataclass(frozen=True)
class RecallEditable(Capability):
    action: str

    def ok(self, actor: FlowActor, doc: Any) -> bool:
        if not doc.is_active or doc.publish_status == "已删除":
            return False
        if self.action == "waybill" and doc.freight_status in ("待审核", "待付款", "已付款"):
            return False
        if doc.completed_at is None or actor.is_star:
            return True
        if self.action in ("waybill", "result") and continuing_published_recall(
            publish_status=doc.publish_status,
            recall_status=doc.recall_status,
            is_active=doc.is_active,
        ):
            return True
        return self.action == "waybill" and doc.freight_status == "已驳回"


@dataclass(frozen=True)
class PaymentQrEditable(Capability):
    """§7.3与四收款码写入口同序；R33状态不能擅自成为全局锁。"""

    def ok(self, actor: FlowActor, doc: Any) -> bool:
        if doc.publish_status in ("未发布", "异常") and doc.recall_status != "召回中":
            return False
        if actor.is_star:
            return True
        if (
            not doc.is_active
            or doc.publish_status == "已删除"
            or doc.stage in ("已停用", "已删除", "已完结 · 不合作", "状态异常")
        ):
            return False
        if doc.settlement_status == "待财务付款":
            return False
        exception = doc.recall_status == "召回中" or doc.freight_status == "已驳回"
        if doc.completed_at is not None and not exception:
            return False
        if doc.promotion_settlement_status in ("已付款", "已结款") and not exception:
            return False
        return exception or doc.stage in (
            "待主管审核",
            "推广驳回 · 待重提",
            "待财务付款",
            "结款驳回 · 待重提",
            "召回中",
        )
