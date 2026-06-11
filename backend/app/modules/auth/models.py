"""auth 模块 ORM 模型（按 functional-design/domain-entities.md）。

10 个实体：
- tenant（租户，根表，无 tenant_id）
- user（继承 TenantScopedModel）
- role（系统级，无 tenant_id）
- permission（系统级，无 tenant_id）
- user_role（继承 TenantScopedModel）
- role_permission（系统级关联，无 tenant_id）
- user_permission_override（继承 TenantScopedModel）
- refresh_token（继承 TenantScopedModel）
- audit_log（特殊：BIGSERIAL 主键，append-only）
- backup_record（系统级，无 tenant_id）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal  # noqa: F401
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, SoftDeleteMixin, TenantScopedModel, TimestampMixin

# ---------------------------------------------------------------------------
# tenant（根表，业务表均依赖）
# ---------------------------------------------------------------------------


class Tenant(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tenant"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_storage_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# role（系统级预设角色）
# ---------------------------------------------------------------------------


class Role(Base, TimestampMixin):
    __tablename__ = "role"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# permission（系统级权限定义）
# ---------------------------------------------------------------------------


class Permission(Base, TimestampMixin):
    __tablename__ = "permission"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="function")


# ---------------------------------------------------------------------------
# role_permission（角色与权限关联，系统级）
# ---------------------------------------------------------------------------


class RolePermission(Base):
    __tablename__ = "role_permission"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("permission.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission_role_perm"),
    )


# ---------------------------------------------------------------------------
# user（业务表，含软删除 + 锁定 + 强制改密 + 安全戳）
# ---------------------------------------------------------------------------


class User(TenantScopedModel, SoftDeleteMixin):
    __tablename__ = "user"

    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    password_must_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),
        Index("ix_user_locked", "tenant_id", "locked_at", postgresql_where=(locked_at.isnot(None))),  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# user_role（业务表，多对多关联）
# ---------------------------------------------------------------------------


class UserRole(TenantScopedModel):
    __tablename__ = "user_role"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("role.id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "role_id", name="uq_user_role_tenant_user_role"
        ),
    )


# ---------------------------------------------------------------------------
# user_permission_override（业务表，自定义权限）
# ---------------------------------------------------------------------------


class UserPermissionOverride(TenantScopedModel):
    __tablename__ = "user_permission_override"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("permission.id", ondelete="RESTRICT"),
        nullable=False,
    )
    effect: Mapped[str] = mapped_column(String(8), nullable=False)  # grant / revoke
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "permission_id",
            name="uq_user_permission_override_tenant_user_perm",
        ),
    )


# ---------------------------------------------------------------------------
# refresh_token（业务表）
# ---------------------------------------------------------------------------


class RefreshToken(TenantScopedModel):
    __tablename__ = "refresh_token"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_refresh_token_user_revoked", "user_id", "revoked_at"),
        Index(
            "ix_refresh_token_active_expiry",
            "expires_at",
            postgresql_where=(revoked_at.is_(None)),  # type: ignore[attr-defined]
        ),
    )


# ---------------------------------------------------------------------------
# audit_log（特殊：BIGSERIAL 主键 + append-only）
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    purpose: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_tenant_action_created", "tenant_id", "action", "created_at"),
        Index("ix_audit_log_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_audit_log_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# backup_record（系统级，无 tenant_id）
# ---------------------------------------------------------------------------


class BackupRecord(Base, TimestampMixin):
    __tablename__ = "backup_record"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    backup_type: Mapped[str] = mapped_column(String(16), nullable=False)  # daily/monthly/manual/restore_drill
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    includes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    r2_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        Index(
            "ix_backup_record_retention",
            "retention_until",
            postgresql_where=(r2_key.isnot(None)),  # type: ignore[attr-defined]
        ),
    )


__all__ = [
    "AuditLog",
    "BackupRecord",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "Tenant",
    "User",
    "UserPermissionOverride",
    "UserRole",
]
