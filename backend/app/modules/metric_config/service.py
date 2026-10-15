"""平台指标配置服务（7e R7）。

``get_settings`` 是所有读取点的唯一入口：每个请求 / 每个任务按租户读一次，再把
``PlatformMetricSettings`` 显式传下去。缺行回落默认，不报错（见 ``settings.py``）。

``update_settings``（``PUT /api/settings/promotion-platforms``，报表视图设计 5.1）：
先拿刷新锁再读现值、比较改动（撞锁 409 时什么都没写）；值没变的行不写。
有任何一行变了，同一个事务里 ① 清汇总覆盖
→ ② 只 upsert 变了的行 → ③ 写审计（改前改后）→ commit；提交后投递
``recompute_blogger_quality_tags``（只重算质量标签），投递失败只记 warning + Sentry，不影响保存。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.auth.models import User
from app.modules.metric_config.repository import PromotionPlatformSettingRepository
from app.modules.metric_config.schemas import (
    PlatformSettingOut,
    PlatformSettingsResponse,
    PlatformSettingsUpdate,
)
from app.modules.metric_config.settings import PlatformMetricSettings
from app.modules.report.exceptions import SummaryRefreshBusyError
from app.modules.report.summary_invalidation import (
    acquire_summary_refresh_lock,
    invalidate_summary_coverage,
)

log = logging.getLogger(__name__)

INVALIDATE_REASON = "platform_metric_setting"


def _values(coef: Decimal, threshold: int) -> dict[str, Any]:
    # 审计 before / after 是 jsonb：系数存定点文本（去尾零），不经 float
    return {"like_coefficient": format(coef.normalize(), "f"), "hit_threshold": threshold}


def _response(
    settings: PlatformMetricSettings,
    *,
    can_edit: bool,
    changed: list[str] | None = None,
    queued: bool = False,
) -> PlatformSettingsResponse:
    return PlatformSettingsResponse(
        items=[PlatformSettingOut.of(p, c, t) for p, c, t in settings.rows()],
        can_edit=can_edit,
        changed=changed or [],
        quality_recompute_queued=queued,
    )


class PlatformMetricSettingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PromotionPlatformSettingRepository(session)
        self._audit = AuditService(session)

    async def get_settings(self, tenant_id: UUID) -> PlatformMetricSettings:
        rows = await self._repo.list_for_tenant(tenant_id)
        return PlatformMetricSettings.from_rows(
            (r.platform, r.like_coefficient, r.hit_threshold) for r in rows
        )

    async def get_response(self, tenant_id: UUID, *, can_edit: bool) -> PlatformSettingsResponse:
        """5 个平台的生效值（没写过行的平台是默认值），不 404。"""
        return _response(await self.get_settings(tenant_id), can_edit=can_edit)

    async def update_settings(
        self, payload: PlatformSettingsUpdate, user: User, *, can_edit: bool
    ) -> PlatformSettingsResponse:
        tenant_id = user.tenant_id
        try:
            # 首次保存可能还没有配置行；与刷新 / 其他保存共用租户锁，拿锁后才读现值。
            await acquire_summary_refresh_lock(self._session, tenant_id=tenant_id)
        except SummaryRefreshBusyError:
            await self._session.rollback()
            raise
        current = await self.get_settings(tenant_id)
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        rows: list[tuple[str, Decimal, int]] = []
        for item in payload.root:
            platform = item.platform.value
            old = (current.like_coefficient(platform), current.hit_threshold(platform))
            new = (item.like_coefficient, item.hit_threshold)
            # Decimal 按数值比：0.1 与 0.100 是同一个系数
            if old == new:
                continue
            before[platform] = _values(*old)
            after[platform] = _values(*new)
            rows.append((platform, item.like_coefficient, item.hit_threshold))
        if not rows:
            return _response(current, can_edit=can_edit)

        # helper 重入同一事务已持有的刷新锁；只有真正有改动才清覆盖。
        invalidated = await invalidate_summary_coverage(
            self._session, tenant_id=tenant_id, reason=INVALIDATE_REASON
        )
        await self._repo.upsert(tenant_id, rows, updated_by=user.id)
        await self._audit.log(
            action="metric_config.update",
            resource="promotion_platform_setting",
            resource_id=tenant_id,
            before=before,
            after={**after, "coverage_invalidated": invalidated},
            user_id=user.id,
            tenant_id=tenant_id,
        )
        await self._session.commit()

        changed = [p for p, _, _ in rows]
        saved = PlatformMetricSettings.from_rows(
            [*current.rows(), *rows]  # 改了的行盖在原值上
        )
        queued = self._enqueue_quality_recompute(tenant_id)
        return _response(saved, can_edit=can_edit, changed=changed, queued=queued)

    @staticmethod
    def _enqueue_quality_recompute(tenant_id: UUID) -> bool:
        """提交后投递只重算质量标签的任务。失败不影响已保存的配置：记 warning + Sentry。"""
        try:
            # 懒导入：tasks 模块拖着 celery app，service 被 import 时不该带上
            from app.tasks.blogger_tasks import recompute_blogger_quality_tags

            recompute_blogger_quality_tags.delay(str(tenant_id))
        except Exception as exc:
            log.warning(
                "platform_metric_quality_recompute_enqueue_failed",
                extra={"tenant_id": str(tenant_id)},
                exc_info=True,
            )
            try:
                sentry_sdk.capture_exception(exc)
            except Exception:  # noqa: S110 Sentry 上报是 best-effort，不得掩盖已保存的结果
                pass
            return False
        return True


__all__ = ["INVALIDATE_REASON", "PlatformMetricSettingService"]
