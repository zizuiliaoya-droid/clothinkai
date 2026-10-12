"""谈款单的矩阵快照（流程线 5.4）与状态机（``allowed_actions``，3.2 转移表）。

快照只放矩阵要读的东西，service 组好再交给 ``flow.matrix`` 的三个出口；纯 Python、不碰库。
import 本模块会顺带 import ``negotiation.flow_matrix``，把谈款矩阵登记进 ``MATRICES``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from app.modules.flow.matrix import FlowDocBase
from app.modules.negotiation import flow_matrix as _flow_matrix  # noqa: F401  登记 MATRICES
from app.modules.negotiation.enums import NegotiationStatus

# 3.2 转移表按「从」归：每个状态下状态机允许的动作键。留言（N11）任意状态都允许（PR-4）、
# 转交（N12）不进矩阵（11-57）
TRANSITIONS: dict[str, frozenset[str]] = {
    NegotiationStatus.DRAFT.value: frozenset({"edit", "submit", "comment"}),  # N2、N3
    NegotiationStatus.PENDING.value: frozenset({"manager_review", "comment"}),  # N4、N5
    NegotiationStatus.REJECTED.value: frozenset({"copy", "comment"}),  # N10（终态，只能复制重谈）
    NegotiationStatus.FINAL_PENDING.value: frozenset({"final_review", "comment"}),  # N6、N7
    NegotiationStatus.FINAL_RETURNED.value: frozenset({"edit", "submit", "comment"}),  # N2、N3
    NegotiationStatus.RECEIVER_PENDING.value: frozenset(
        {"receiver_save", "receiver_confirm", "comment"}
    ),  # N8、N9
    NegotiationStatus.APPROVED.value: frozenset({"comment"}),  # 终态
}


@dataclass(frozen=True, kw_only=True)
class NegotiationDoc(FlowDocBase):
    """谈款快照。``stage`` = ``state`` = ``column`` = 状态存储值；``owner_id`` = ``negotiator_id`` = ``pr_id``。

    ★¹ 各项用表单字段同名属性（``Gate`` 默认按 ``bool(getattr(doc, key))`` 判），颜色尺码看 ``items_complete``。
    """

    kind: ClassVar[str] = "negotiation"
    reviewed_by: UUID | None
    """本轮主管审核人（``NotFirstReviewer``）。"""
    blogger_id: UUID | None
    style_id: UUID | None
    items_complete: bool
    """颜色尺码齐：明细的款式集合 = 商品的启用成员，且每行 sku 属于该行款式（N3）。"""
    cooperation_mode: str | None
    platform: str | None
    scheduled_publish_date: date | None
    quote_amount: Decimal | None
    receiver_name: str | None
    receiver_phone: str | None
    receiver_address: str | None

    def allowed_actions(self) -> frozenset[str]:
        return TRANSITIONS.get(self.state, frozenset())


_DOC_COLUMNS: tuple[str, ...] = (
    "reviewed_by",
    "blogger_id",
    "style_id",
    "cooperation_mode",
    "platform",
    "scheduled_publish_date",
    "quote_amount",
    "receiver_name",
    "receiver_phone",
    "receiver_address",
)


def build_negotiation_doc(source: Any, *, items_complete: bool) -> NegotiationDoc:
    """``source``：谈款 ORM 对象，或列表 / 详情查询的一行（``Mapping``，列名同 ORM 属性）。

    ``items_complete`` 由调用方按本页明细与商品成员算（``promotion.flow_doc.goods_items_complete`` + sku 归属）。
    """

    def get(name: str) -> Any:
        return source[name] if isinstance(source, Mapping) else getattr(source, name)

    status = get("status")
    pr_id = get("pr_id")
    return NegotiationDoc(
        stage=status,
        state=status,
        column=status,
        owner_id=pr_id,
        negotiator_id=pr_id,
        items_complete=items_complete,
        **{name: get(name) for name in _DOC_COLUMNS},
    )


__all__ = ["TRANSITIONS", "NegotiationDoc", "build_negotiation_doc"]
