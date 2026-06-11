"""U07 wecom ORM 模型（5 表）。

按 functional-design/domain-entities.md 定义，继承 ``TenantScopedModel``（U01）：
自动 id(UUID PK) + tenant_id(FK + ORM 钩子) + created_at/updated_at，启用 RLS。

- wecom_config：企微自建应用配置（UNIQUE tenant_id，secret 密文）
- wecom_contact：博主 ↔ 企微外部联系人绑定（UNIQUE tenant_id+blogger_id）
- message_template：催发模板（UNIQUE tenant_id+template_type）
- wecom_message：群发消息记录（6 态状态机，频控复合索引）
- notification：站内通知（本人未读索引）
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class WecomConfig(TenantScopedModel):
    """企微自建应用配置（单租户单条）。"""

    __tablename__ = "wecom_config"

    corp_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    callback_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    callback_aes_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_sender_userid: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        Index("uq_wecom_config_tenant", "tenant_id", unique=True),
    )


class WecomContact(TenantScopedModel):
    """博主 ↔ 企微外部联系人绑定（一博主一绑定，可重绑覆盖）。"""

    __tablename__ = "wecom_contact"

    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_userid: Mapped[str] = mapped_column(String(128), nullable=False)
    matched_wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("uq_wecom_contact_blogger", "tenant_id", "blogger_id", unique=True),
        Index("idx_wecom_contact_external", "tenant_id", "external_userid"),
    )


class MessageTemplate(TenantScopedModel):
    """催发消息模板（按类型唯一）。"""

    __tablename__ = "message_template"

    template_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "uq_message_template_type", "tenant_id", "template_type", unique=True
        ),
    )


class WecomMessage(TenantScopedModel):
    """企微群发消息记录（6 态状态机，永久留痕无 is_active）。"""

    __tablename__ = "wecom_message"

    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    template_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rendered_content: Mapped[str] = mapped_column(Text, nullable=False)
    promotion_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    wecom_msgid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "idx_wecom_message_blogger", "tenant_id", "blogger_id", "created_at"
        ),
        Index("idx_wecom_message_pr", "tenant_id", "pr_id", "created_at"),
        Index("idx_wecom_message_status", "tenant_id", "status"),
        Index("idx_wecom_message_msgid", "wecom_msgid"),
    )


class Notification(TenantScopedModel):
    """站内通知（MVP 首个消费者 = 频控降级）。"""

    __tablename__ = "notification"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index(
            "idx_notification_user",
            "tenant_id",
            "user_id",
            "is_read",
            "created_at",
        ),
    )


__all__ = [
    "MessageTemplate",
    "Notification",
    "WecomConfig",
    "WecomContact",
    "WecomMessage",
]
