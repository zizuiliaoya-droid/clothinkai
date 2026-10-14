"""推广单 / 仓库行的矩阵快照（流程线 5.4）与状态机（``allowed_actions``）。

快照只放矩阵要读的东西，service 组好再交给 ``flow.matrix`` 的三个出口；纯 Python、不碰库。
import 本模块会顺带 import ``promotion.flow_matrix``，把两张矩阵登记进 ``MATRICES``。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.modules.flow.matrix import FlowDocBase
from app.modules.promotion import flow_matrix as _flow_matrix  # noqa: F401  登记 MATRICES
from app.modules.promotion.enums import PublishStatus, RecallStatus, ShipStatus
from app.modules.promotion.models import Promotion
from app.modules.promotion.stage_calculator import STAGE_COLUMN, metrics_available_from

_UNPUBLISHED = PublishStatus.UNPUBLISHED.value
_NOT_RECALLED = RecallStatus.NOT_RECALLED.value
# 能催发的发布状态，与 urge.service._URGEABLE_PUBLISH_STATUS / _load_urgeable_promotion 一致
_URGEABLE = frozenset({PublishStatus.UNPUBLISHED.value, PublishStatus.ABNORMAL.value})
# PR-6 推广审核与结款链：只在一个精确阶段允许的动作（3.4 R2 ~ R9、3.7）。阶段按 3.8 的顺序已排除
# 删除 / 停用 / 召回中 / 完结，所以「推广单结款状态 = X」在这里等同「阶段 = X 对应的那一个」
_STAGE_ACTIONS: dict[str, str] = {
    "待主管审核": "review",
    "结款驳回 · 待重提": "settlement_resubmit",
    "待传结款截图": "settlement_chat",
    "待录 7 天数据": "metrics",
}


@dataclass(frozen=True, kw_only=True)
class PromotionDoc(FlowDocBase):
    """推广单快照。``stage`` = 3.8 派生阶段，``state`` 同 ``stage``，``column`` = A ~ H。

    结款有两个状态（PR-6）：``settlement_status`` 是这张推广单**结款单**的状态（谓词 ``SettlementIn``，
    还没有结款单为 None），``promotion_settlement_status`` 是推广单自己的（谓词 ``PromotionSettlementIn``）。
    """

    kind: ClassVar[str] = "promotion"
    publish_status: str
    recall_status: str
    ship_status: str | None
    is_active: bool
    receiver_name: str | None
    receiver_phone: str | None
    receiver_address: str | None
    items_complete: bool
    """颜色尺码明细齐：款式集合 = 归属商品的启用成员（单品 1 行、套装每个成员 1 行）。"""
    cooperation_mode: str | None
    """合作模式；为空时主管 / 管理员可补一次（5.3 ²³，谓词 ``ModeIn``）。"""
    promotion_settlement_status: str
    """推广单自己的结款状态（3.4 的 7 个值）。"""
    settlement_status: str | None
    """结款单状态；还没有结款单为 None。"""
    quote_amount: Decimal | None
    """服务费（= 审核时的应付）：> 0 才要收款码（★¹⁵）。"""
    has_payment_qr: bool
    return_waybill: str | None
    """寄拍寄回单号（★¹⁵ 寄拍要有寄回单号，或召回成功且有召回寄回截图）。"""
    has_recall_waybill: bool
    """有召回寄回截图。"""
    metrics_available_from: date | None
    """7 天数据可以录的第一天（发布日 + 7），灰格提示用；没有发布日为 None。"""

    def _open_unpublished(self) -> bool:
        return (
            self.publish_status == _UNPUBLISHED
            and self.recall_status == _NOT_RECALLED
            and self.is_active
        )

    def allowed_actions(self) -> frozenset[str]:
        """细化 §6 的状态机列。PR-2 有发货三个动作、PR-4 有留言（任何阶段都允许）、PR-5 有催发与停用，
        PR-6 有推广审核、结款重提、结款聊天截图、录 7 天数据（各只在一个阶段，``_STAGE_ACTIONS``），
        其余动作随各自的 PR 入矩阵。

        「标记完成」（``urge_complete``）不在这里：它的状态在催发任务上（进行中才能点），按钮只在催发页。
        """
        allowed: set[str] = {"comment"}
        if self.is_active and self.publish_status in _URGEABLE:
            allowed.add("urge")
        if self.is_active and self.publish_status != PublishStatus.DELETED.value:
            allowed.add("deactivate")
        if self.ship_status == ShipStatus.PENDING.value and self._open_unpublished():
            allowed.add("ship_push")
        if self.ship_status == ShipStatus.PRINTING.value:
            allowed.add("ship_withdraw")
        if self.ship_status is None and self._open_unpublished():
            allowed.add("ship_include")
        stage_action = _STAGE_ACTIONS.get(self.stage)
        if stage_action is not None:
            allowed.add(stage_action)
        return frozenset(allowed)


@dataclass(frozen=True, kw_only=True)
class WarehouseDoc(FlowDocBase):
    """仓库行快照（仓库页 / 回填）。阶段用推广单同一套。"""

    kind: ClassVar[str] = "warehouse"
    ship_status: str | None

    def allowed_actions(self) -> frozenset[str]:
        if self.ship_status in (ShipStatus.PRINTING.value, ShipStatus.SHIPPED.value):
            return frozenset({"ship_fill"})
        return frozenset()


def goods_items_complete(item_style_ids: Iterable[UUID], members: Iterable[UUID]) -> bool:
    """明细的款式集合与成员集合相等（没有明细 = 不齐）。"""
    given = list(item_style_ids)
    return bool(given) and set(given) == set(members)


def build_promotion_doc(
    promotion: Promotion,
    *,
    stage: str,
    negotiator_id: UUID | None,
    items_complete: bool,
    settlement_status: str | None,
) -> PromotionDoc:
    """``negotiator_id``：这张单谈款的 PR；没有谈款（导入、主管补录）回落到负责 PR（L1）。

    ``settlement_status``：结款单状态（没有结款单传 None），不给默认值——漏传会让收款码锁静默放开。
    """
    return PromotionDoc(
        stage=stage,
        state=stage,
        column=STAGE_COLUMN[stage],
        owner_id=promotion.pr_id,
        negotiator_id=negotiator_id if negotiator_id is not None else promotion.pr_id,
        publish_status=promotion.publish_status,
        recall_status=promotion.recall_status,
        ship_status=promotion.ship_status,
        is_active=promotion.is_active,
        receiver_name=promotion.receiver_name,
        receiver_phone=promotion.receiver_phone,
        receiver_address=promotion.receiver_address,
        items_complete=items_complete,
        cooperation_mode=promotion.cooperation_mode,
        promotion_settlement_status=promotion.settlement_status,
        settlement_status=settlement_status,
        quote_amount=promotion.quote_amount,
        has_payment_qr=promotion.payment_qr_attachment_id is not None,
        return_waybill=promotion.return_waybill,
        has_recall_waybill=promotion.recall_waybill_attachment_id is not None,
        metrics_available_from=metrics_available_from(promotion.actual_publish_date),
    )


def build_warehouse_doc(*, stage: str, pr_id: UUID | None, ship_status: str | None) -> WarehouseDoc:
    """仓库行快照。入参是散的：仓库页只取投影列（``WarehouseShipmentRecord``），回填前取的是整张单。"""
    return WarehouseDoc(
        stage=stage,
        state=stage,
        column=STAGE_COLUMN[stage],
        owner_id=pr_id,
        negotiator_id=pr_id,
        ship_status=ship_status,
    )


__all__ = [
    "PromotionDoc",
    "WarehouseDoc",
    "build_promotion_doc",
    "build_warehouse_doc",
    "goods_items_complete",
]
