"""跨环节事件表 ORM（流程线设计 2.3、第 6 节）。

一张表按 ``line_id`` 串谈款与推广单：谈款的提交 / 审核 / 退回、留言贴图、推广单各环节都写这里，时间线一次按主线读完。
只追加：仓储只暴露 append / list_by_line，库里 ``clothing_app`` 也没有 UPDATE / DELETE（迁移 063）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

# FK 目标表 attachment / user / negotiation / promotion：Celery 路径不一定经过这些模块，
# 不显式 import 的话 flush LineEvent 时 NoReferencedTableError（台账）
from app.core import attachment as _attachment_models  # noqa: F401
from app.core.db import TenantScopedModel
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.negotiation import models as _negotiation_models  # noqa: F401
from app.modules.promotion import models as _promotion_models  # noqa: F401


class LineEvent(TenantScopedModel):
    """主线上的一条事件。"""

    __tablename__ = "line_event"

    # seq 是 identity（库里发号）：同一事务写的几条事件 created_at 可能相同，排序用 seq。
    # eager_defaults 让 INSERT 带 RETURNING 取回 seq / payload 默认值，避免 async 下延迟加载（MissingGreenlet）
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    line_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    """主线号。不建 FK（两头都有：谈款、推广单）。"""
    negotiation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("negotiation.id", ondelete="RESTRICT"),
        nullable=True,
    )
    promotion_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="RESTRICT"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    """取值表在 ``line_events.LineEventType``；库里不加 CHECK，加类型不用 DDL。"""
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    """审核意见 / 退回原因 / 重提说明 / 留言。"""
    attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    """改了什么（字段前后值）、金额等；读接口按字段级权限裁剪。"""
    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """NULL = 系统。"""

    __table_args__ = (
        Index("idx_line_event_line", "tenant_id", "line_id", "seq"),
        CheckConstraint(
            "negotiation_id IS NOT NULL OR promotion_id IS NOT NULL",
            name="ck_line_event_owner",
        ),
    )


__all__ = ["LineEvent"]
