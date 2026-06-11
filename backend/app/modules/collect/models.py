"""U13 采集 ORM 模型（5 表，全部 TenantScopedModel + RLS）。

- WorkerToken：Worker 鉴权（sha256 token + IP allowlist）
- CrawlerTask：采集任务队列（pull 模型 + 一次性 cred_token）
- DataQualityIssue：数据质量异常（info/warning/error）
- QianniuDaily / AdDaily：采集数据落库（UNIQUE 幂等）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class WorkerToken(TenantScopedModel):
    """采集 Worker 鉴权令牌（独立于用户 JWT）。"""

    __tablename__ = "worker_token"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_allowlist: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    consecutive_auth_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_worker_token_hash", "tenant_id", "token_hash", unique=True),
        Index("idx_worker_token_active", "tenant_id", "is_active"),
    )


class CrawlerTask(TenantScopedModel):
    """采集任务（pull 队列 + 一次性 cred_token）。"""

    __tablename__ = "crawler_task"

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("credential.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    worker_token_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worker_token.id", ondelete="SET NULL"),
        nullable=True,
    )
    cred_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cred_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    import_batch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        Index(
            "uq_crawler_task_tenant_plat_cred_date",
            "tenant_id", "platform", "credential_id", "target_date",
            unique=True,
        ),
        Index("idx_crawler_task_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN ('pending','assigned','exchanged','success','failed')",
            name="ck_crawler_task_status",
        ),
    )


class DataQualityIssue(TenantScopedModel):
    """数据质量异常（采集/导入未匹配等）。"""

    __tablename__ = "data_quality_issue"

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'open'")
    )
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_dq_tenant_source_sev", "tenant_id", "source", "severity"),
        Index("idx_dq_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "severity IN ('info','warning','error')", name="ck_dq_severity"
        ),
        CheckConstraint(
            "status IN ('open','fixed','ignored')", name="ck_dq_status"
        ),
    )


class QianniuDaily(TenantScopedModel):
    """千牛商品日报（S11 落库）。"""

    __tablename__ = "qianniu_daily"

    platform_product_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_product.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform_id_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    visitors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pay_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    pay_orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index(
            "uq_qianniu_daily_tenant_pid_date",
            "tenant_id", "platform_id_snapshot", "date",
            unique=True,
        ),
        Index("idx_qianniu_daily_date", "tenant_id", "date"),
    )


class AdDaily(TenantScopedModel):
    """万相台广告日报（S12 落库）。"""

    __tablename__ = "ad_daily"

    platform_product_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_product.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform_id_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gmv: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index(
            "uq_ad_daily_tenant_pid_date",
            "tenant_id", "platform_id_snapshot", "date",
            unique=True,
        ),
        Index("idx_ad_daily_date", "tenant_id", "date"),
    )


__all__ = [
    "AdDaily",
    "CrawlerTask",
    "DataQualityIssue",
    "QianniuDaily",
    "WorkerToken",
]
