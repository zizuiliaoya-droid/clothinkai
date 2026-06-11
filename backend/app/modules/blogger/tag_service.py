"""U11 博主智能标签服务（BloggerTagService）。

按 P-U11-01 / P-U11-02：
- compute_blogger_type：实时 O(1) 粉丝量分级（KOL/KOC/素人）。
- compute_read_like_ratio：读时衍生点赞/阅读比（不存 DB），分母 0/None→None。
- is_fake_account：ratio≤阈值→True；None（无数据）→False（保守）。
- recompute_for_tenant：批量重算单租户全部活跃博主，单 blogger 失败不中止。

质量标签聚合委托 ``services/metric/blogger_quality.compute_quality_tags``。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.repository import BloggerRepository
from app.modules.blogger.tag_config import (
    FAKE_RATIO_THRESHOLD,
    FOLLOWER_KOC_MIN,
    FOLLOWER_KOL_MIN,
)
from app.services.metric.blogger_quality import compute_quality_tags

log = logging.getLogger(__name__)


class BloggerTagService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BloggerRepository(session)

    # ------------------------------------------------------------------ #
    # 纯函数（实时 / 读时衍生）
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_blogger_type(follower_count: int | None) -> str | None:
        """粉丝量分级（实时 O(1)）。follower_count 为 None → None。"""
        if follower_count is None:
            return None
        if follower_count >= FOLLOWER_KOL_MIN:
            return "KOL"
        if follower_count >= FOLLOWER_KOC_MIN:
            return "KOC"
        return "素人"

    @staticmethod
    def compute_read_like_ratio(
        audience_profile: dict | None,
    ) -> Decimal | None:
        """点赞/阅读比（读时衍生，不存 DB）。

        从 audience_profile.note_stats.{avg_likes, avg_reads} 计算。
        缺数据 / avg_reads 为 0 → None。
        """
        if not audience_profile:
            return None
        stats = audience_profile.get("note_stats")
        if not stats:
            return None
        avg_reads = stats.get("avg_reads", 0)
        if not avg_reads:
            return None
        avg_likes = stats.get("avg_likes", 0)
        return Decimal(str(avg_likes)) / Decimal(str(avg_reads))

    @staticmethod
    def is_fake_account(ratio: Decimal | None) -> bool:
        """假号嫌疑判定。无数据（ratio None）→ False（保守不标记）。"""
        if ratio is None:
            return False
        return ratio <= FAKE_RATIO_THRESHOLD

    # ------------------------------------------------------------------ #
    # 批量重算（Celery 调用）
    # ------------------------------------------------------------------ #

    async def recompute_for_tenant(self, tenant_id: UUID) -> dict[str, int]:
        """重算单租户全部活跃博主的标签。

        单 blogger 失败（catch+log）不中止；返回 updated/failed 计数。
        调用方负责 commit。
        """
        bloggers = await self._repo.list_active_bloggers(tenant_id)
        updated = 0
        failed = 0
        for b in bloggers:
            try:
                b.blogger_type = self.compute_blogger_type(b.follower_count)
                ratio = self.compute_read_like_ratio(b.audience_profile)
                b.is_suspected_fake = self.is_fake_account(ratio)
                b.quality_tags = await compute_quality_tags(
                    b.id, self._session, tenant_id
                )
                updated += 1
            except Exception:  # noqa: BLE001 单 blogger 失败不影响其余
                log.warning(
                    "recompute_blogger_failed blogger_id=%s", str(b.id)
                )
                failed += 1
        await self._session.flush()
        return {"updated": updated, "failed": failed}


__all__ = ["BloggerTagService"]
