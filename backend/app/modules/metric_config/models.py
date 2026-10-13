"""平台指标配置 ORM（迁移 066，报表视图设计 2.7 / 2.8 / 3.4）。

每租户每平台一行：``hit_threshold`` 是爆文阈值（原始点赞，不折算），``like_coefficient`` 是点赞折算系数。
迁移给现有租户各插 5 行默认值；缺行时读取侧回落默认、不报错（新租户首次保存时补行）。
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

# FK 目标表（tenant、user）必须在同一份 metadata 里，否则只走 Celery import 链时 flush 抛
# NoReferencedTableError（见 report/summary_models.py 的注释）。这行不是多余的 import。
import app.modules.auth.models  # noqa: F401  (tenant / user)
from app.core.db import TenantScopedModel


class PromotionPlatformSetting(TenantScopedModel):
    """一个租户、一个平台的爆文阈值与点赞折算系数。"""

    __tablename__ = "promotion_platform_setting"

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    like_coefficient: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    hit_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "uq_promotion_platform_setting_tenant_platform", "tenant_id", "platform", unique=True
        ),
        # 命名约定套前缀：实名 ck_promotion_platform_setting_like_coefficient / _hit_threshold
        CheckConstraint("like_coefficient > 0 AND like_coefficient <= 10", name="like_coefficient"),
        CheckConstraint("hit_threshold >= 1 AND hit_threshold <= 10000000", name="hit_threshold"),
    )


__all__ = ["PromotionPlatformSetting"]
