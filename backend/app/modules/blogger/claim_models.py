"""租户内非独占对接关系；工作微信复用租户字典，不存密码或博主微信。"""

from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class BloggerClaim(TenantScopedModel):
    __tablename__ = "blogger_claim"

    blogger_id: Mapped[UUID] = mapped_column(ForeignKey("blogger.id", ondelete="RESTRICT"))
    pr_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="RESTRICT"))
    method: Mapped[str] = mapped_column(String(24))
    operated_by: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="RESTRICT"))
    work_wechat_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dict_item.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        Index("uq_blogger_claim", "tenant_id", "blogger_id", "pr_id", unique=True),
        Index("ix_blogger_claim_pr", "tenant_id", "pr_id", "blogger_id"),
        ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_claim_blogger_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pr_id"], ["user.tenant_id", "user.id"], name="fk_claim_pr_tenant"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "operated_by"],
            ["user.tenant_id", "user.id"],
            name="fk_claim_actor_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "work_wechat_id"],
            ["dict_item.tenant_id", "dict_item.id"],
            name="fk_claim_work_tenant",
        ),
    )
