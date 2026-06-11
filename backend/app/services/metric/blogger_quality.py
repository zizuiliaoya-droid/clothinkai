"""U11 博主质量聚合（services/metric）。

按 P-U11-02：聚合 promotion 历史计算 avg CPL / hit_rate，用于质量标签判定。

关键防御：
- 显式 ``WHERE tenant_id = :tid``（测试引擎 bypass 角色 RLS OFF，全局聚合必须显式过滤）。
- ``LIMIT QUALITY_AGG_LIMIT`` 截断超大历史，保证 ≤200ms。
- CPL 折算复用 U04 ``metrics_calculator``（平台系数）；hit 用原始 like_count 与阈值比较。
- safe_div（复用 U08 services/metric/common）分母 0/None→None。
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.tag_config import (
    HIGH_CPL_THRESHOLD,
    HIT_RATE_THRESHOLD,
    QUALITY_AGG_LIMIT,
    TAG_BESTSELLER,
    TAG_HIGH_VALUE,
)
from app.modules.promotion.legacy_settings import HIT_THRESHOLD_LIKE_COUNT
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
    calculate_is_hit,
)
from app.modules.promotion.models import Promotion
from app.services.metric.common import safe_div


async def _load_promotions(
    blogger_id: UUID, session: AsyncSession, tenant_id: UUID
) -> list[Promotion]:
    """加载该博主最近的有效推广（显式 tenant + LIMIT 截断）。"""
    stmt = (
        select(Promotion)
        .where(
            Promotion.tenant_id == tenant_id,
            Promotion.blogger_id == blogger_id,
            Promotion.is_active.is_(True),
        )
        .order_by(Promotion.cooperation_date.desc())
        .limit(QUALITY_AGG_LIMIT)
    )
    return list((await session.execute(stmt)).scalars().all())


async def avg_cpl_for_blogger(
    blogger_id: UUID, session: AsyncSession, tenant_id: UUID
) -> Decimal | None:
    """该博主历史推广的平均单赞成本（CPL）。

    逐行折算 effective_like_count → CPL，对非 None 的 CPL 求平均。
    无有效样本（无推广 / 全部点赞为 None/0）→ None。
    """
    rows = await _load_promotions(blogger_id, session, tenant_id)
    cpls: list[Decimal] = []
    for p in rows:
        eff = calculate_effective_like_count(
            platform=p.platform, like_count=p.like_count
        )
        cpl = calculate_cpl(quote_amount=p.quote_amount, effective_like_count=eff)
        if cpl is not None:
            cpls.append(cpl)
    if not cpls:
        return None
    return safe_div(sum(cpls), len(cpls), quantize=Decimal("0.0001"))


async def hit_rate_for_blogger(
    blogger_id: UUID, session: AsyncSession, tenant_id: UUID
) -> Decimal | None:
    """该博主历史推广的爆文率（is_hit 数 / 总数）。

    无推广样本 → None。
    """
    rows = await _load_promotions(blogger_id, session, tenant_id)
    if not rows:
        return None
    hit_count = sum(
        1
        for p in rows
        if calculate_is_hit(
            like_count=p.like_count, threshold=HIT_THRESHOLD_LIKE_COUNT
        )
    )
    return safe_div(hit_count, len(rows), quantize=Decimal("0.0001"))


async def compute_quality_tags(
    blogger_id: UUID, session: AsyncSession, tenant_id: UUID
) -> list[str]:
    """按聚合结果生成质量标签（多标签可叠加）。"""
    tags: list[str] = []
    cpl = await avg_cpl_for_blogger(blogger_id, session, tenant_id)
    if cpl is not None and cpl <= HIGH_CPL_THRESHOLD:
        tags.append(TAG_HIGH_VALUE)
    hit = await hit_rate_for_blogger(blogger_id, session, tenant_id)
    if hit is not None and hit >= HIT_RATE_THRESHOLD:
        tags.append(TAG_BESTSELLER)
    return tags


__all__ = [
    "avg_cpl_for_blogger",
    "compute_quality_tags",
    "hit_rate_for_blogger",
]
