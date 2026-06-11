"""U15 企微进阶 ORM 模型（2 表，TenantScopedModel + RLS）。

- WecomAlertConfig：控评群机器人 webhook + 异常预警阈值 + 接收人 + 开关（UNIQUE tenant）
- WecomAlertLog：异常预警去重留痕（UNIQUE tenant,alert_type,entity_ref,period_key）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class WecomAlertConfig(TenantScopedModel):
    """企微预警配置（单租户单条）。"""

    __tablename__ = "wecom_alert_config"

    control_group_webhook: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_rate_threshold: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("0.4000")
    )
    low_roi_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4), nullable=True
    )
    low_conversion_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    alert_recipients: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        Index("uq_wecom_alert_config_tenant", "tenant_id", unique=True),
    )


class WecomAlertLog(TenantScopedModel):
    """异常预警去重留痕（同款式同类型当日 ≤1）。"""

    __tablename__ = "wecom_alert_log"

    alert_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_key: Mapped[str] = mapped_column(String(10), nullable=False)
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "uq_wecom_alert_log",
            "tenant_id", "alert_type", "entity_ref", "period_key",
            unique=True,
        ),
        Index("idx_wecom_alert_log_fired", "tenant_id", "fired_at"),
    )


__all__ = ["WecomAlertConfig", "WecomAlertLog"]
