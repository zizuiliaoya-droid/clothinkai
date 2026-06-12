"""U03 ORM 模型：Blogger。

按 functional-design/domain-entities.md §3 定义。

继承 ``TenantScopedModel``：
- 自动 ``id`` (UUID PK) + ``tenant_id`` (UUID FK + ORM 钩子)
- 自动 ``created_at`` / ``updated_at``
- 启用 RLS（migration 通过 ``rls.enable_rls_sql`` 配置）

业务键唯一约束：
- ``(tenant_id, xiaohongshu_id) WHERE is_deleted=false``

GIN 索引（U03 强制建）：
- ``idx_blogger_nickname_trgm``：单字段 GIN trgm（不拼接，数据量小）
- ``idx_blogger_xhs_id_trgm``：单字段 GIN trgm
- ``idx_blogger_category_tags`` / ``idx_blogger_quality_tags``：JSONB GIN
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class Blogger(TenantScopedModel):
    """博主基础信息。"""

    __tablename__ = "blogger"

    xiaohongshu_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'小红书'")
    )
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blogger_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gender_target: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category_tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    quality_tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    quote: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    cooperation_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_suspected_fake: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    # U11：受众画像（U13 采集 Worker 写入，U11 仅读展示 read_like_ratio）
    audience_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 灰豚爬虫指标（对齐 final.xlsx 博主库 41 列：3/7/14篇互动、粉丝画像、涨跌等）
    crawler_metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    __table_args__ = (
        # 部分唯一索引：软删后 xiaohongshu_id 释放
        Index(
            "uq_blogger_xiaohongshu_id",
            "tenant_id",
            "xiaohongshu_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "idx_blogger_tenant_active",
            "tenant_id",
            "is_active",
            "is_deleted",
        ),
        Index("idx_blogger_type", "tenant_id", "blogger_type"),
        Index("idx_blogger_follower_count", "tenant_id", "follower_count"),
        Index("idx_blogger_platform", "tenant_id", "platform"),
        Index(
            "idx_blogger_suspected_fake",
            "tenant_id",
            postgresql_where=text("is_suspected_fake = true"),
        ),
        # GIN trgm + GIN JSONB 索引在 alembic migration 中通过 op.execute 创建
        # （SQLAlchemy 表达式索引声明在某些版本组合下繁琐）
        CheckConstraint(
            "follower_count IS NULL OR follower_count >= 0",
            name="ck_blogger_follower_count_nonneg",
        ),
        CheckConstraint(
            "quote IS NULL OR quote >= 0", name="ck_blogger_quote_nonneg"
        ),
    )


__all__ = ["Blogger"]
