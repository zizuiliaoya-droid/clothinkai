"""推广单 / 仓库行的矩阵快照（流程线 5.4）与状态机（``allowed_actions``）。

快照只放矩阵要读的东西，service 组好再交给 ``flow.matrix`` 的三个出口；纯 Python、不碰库。
import 本模块会顺带 import ``promotion.flow_matrix``，把两张矩阵登记进 ``MATRICES``。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.modules.flow.matrix import FlowDocBase
from app.modules.promotion import flow_matrix as _flow_matrix  # noqa: F401  登记 MATRICES
from app.modules.promotion.enums import PublishStatus, RecallStatus, ShipStatus
from app.modules.promotion.models import Promotion
from app.modules.promotion.stage_calculator import STAGE_COLUMN

_UNPUBLISHED = PublishStatus.UNPUBLISHED.value
_NOT_RECALLED = RecallStatus.NOT_RECALLED.value
# 能催发的发布状态，与 urge.service._URGEABLE_PUBLISH_STATUS / _load_urgeable_promotion 一致
_URGEABLE = frozenset({PublishStatus.UNPUBLISHED.value, PublishStatus.ABNORMAL.value})


@dataclass(frozen=True, kw_only=True)
class PromotionDoc(FlowDocBase):
    """推广单快照。``stage`` = 3.8 派生阶段，``state`` 同 ``stage``，``column`` = A ~ H。

    不放推广单的 ``settlement_status``：矩阵的 ``SettlementIn`` 指结款单状态（PR-6）。
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

    def _open_unpublished(self) -> bool:
        return (
            self.publish_status == _UNPUBLISHED
            and self.recall_status == _NOT_RECALLED
            and self.is_active
        )

    def allowed_actions(self) -> frozenset[str]:
        """细化 §6 的状态机列。PR-2 有发货三个动作、PR-4 有留言（任何阶段都允许）、PR-5 有催发与停用，
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
) -> PromotionDoc:
    """``negotiator_id``：这张单谈款的 PR；没有谈款（导入、主管补录）回落到负责 PR（L1）。"""
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
