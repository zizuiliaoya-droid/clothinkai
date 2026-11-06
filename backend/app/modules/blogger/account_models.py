"""账号仍是 Blogger；关系与标价不改变原账号和历史单据。"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class BloggerAccountGroup(TenantScopedModel):
    __tablename__ = "blogger_account_group"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (Index("uq_account_group_tenant_id", "tenant_id", "id", unique=True),)


class BloggerAccountMember(TenantScopedModel):
    __tablename__ = "blogger_account_member"
    group_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    blogger_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["blogger_account_group.tenant_id", "blogger_account_group.id"],
            ondelete="RESTRICT",
            name="fk_account_member_group",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            ondelete="RESTRICT",
            name="fk_account_member_blogger",
        ),
        Index("uq_account_member_group", "tenant_id", "group_id", "blogger_id", unique=True),
        Index(
            "uq_account_member_active",
            "tenant_id",
            "blogger_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class BloggerAccountQuote(TenantScopedModel):
    __tablename__ = "blogger_account_quote"
    group_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    account_key: Mapped[str] = mapped_column(String(369), nullable=False)
    account_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["blogger_account_group.tenant_id", "blogger_account_group.id"],
            ondelete="RESTRICT",
            name="fk_account_quote_group",
        ),
        Index("uq_account_quote_key", "tenant_id", "group_id", "account_key", unique=True),
        CheckConstraint("amount >= 0", name="ck_account_quote_nonnegative"),
    )
