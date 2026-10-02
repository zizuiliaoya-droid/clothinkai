"""催发任务服务层（PRD V1.4 改动 2）。

职责边界：本模块管任务与留痕，``wecom`` 管企微投递。生产上 ``wecom_config`` 一直是
0 行、``wecom_message`` 0 行 —— 那条通知链路从未发出过消息。所以这里的每个操作都
不依赖企微：手动催发、留痕、计次、关闭、看板统计全部独立成立，企微可用时才在留痕上
关联一条 ``wecom_message``。

PRD 改动 2 的六条要求各自落在哪：

1. 手动随时发起（单条 + 按款式批量）→ ``urge_once`` / ``urge_batch``
2. 自动按临期触发（≤ N 天未发布，阈值可配）→ ``scan_tenant``，阈值读 ``urge_config``
3. 每次催发留痕（截图 + 时间戳 + 备注，一单多次，时间线倒序）→ ``urge_record``
4. 博主确认发布 → 任务自动关闭 → ``close_for_promotion``，由 publish / cancel 调用
5. 超过 N 次提示主管 → 响应里的 ``over_limit``（服务端算，不让前端拿阈值自己比）
6. 主管看板 → ``dashboard``
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import AttachmentService, attachment_service, check_image_payload
from app.core.audit import AuditService
from app.modules.auth.models import User
from app.modules.promotion.urge_calculator import UrgeThresholds, get_today
from app.modules.urge.enums import UrgeCloseReason, UrgeTaskStatus, UrgeTriggerType
from app.modules.urge.exceptions import (
    UrgeBatchEmptyError,
    UrgeNotApplicableError,
    UrgeScreenshotInvalidError,
    UrgeTaskClosedError,
    UrgeTaskNotFoundError,
)
from app.modules.urge.models import UrgeConfig, UrgeRecord
from app.modules.urge.repository import (
    UrgeConfigRepository,
    UrgeRecordRepository,
    UrgeTaskRepository,
)
from app.modules.urge.schemas import (
    UrgeBatchResponse,
    UrgeConfigResponse,
    UrgeConfigUpdate,
    UrgeDashboardResponse,
    UrgeRecordResponse,
    UrgeScanResult,
    UrgeTaskDetailResponse,
    UrgeTaskListFilters,
    UrgeTaskResponse,
)

log = logging.getLogger(__name__)

_SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024
_SCREENSHOT_PURPOSE = "urge_screenshot"

# 催发任务只对「还没发出来」的单据成立。已发布/已取消/已删除建任务没有意义，
# 还会把看板的「待催发」数字搞脏。
_URGEABLE_PUBLISH_STATUS = ("未发布", "异常")

# 配置缺行时的回退默认值。与 DB server_default、Pydantic Field 默认三处一致。
# migration 049 已为现存租户写了行，这里兜的是新租户还没写配置的空窗。
_FALLBACK = {
    "no_publish_days": 5,
    "max_urge_times": 3,
    "max_overdue_days": 30,
    "urge_threshold_days": 10,
    "important_threshold_days": 3,
    "auto_scan_enabled": True,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _week_start(today: date) -> date:
    """本周一。PRD 看板的「本周已催发」以自然周算。"""
    return today - timedelta(days=today.weekday())


class UrgeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        attachments: AttachmentService | None = None,
    ) -> None:
        self._session = session
        self._tasks = UrgeTaskRepository(session)
        self._records = UrgeRecordRepository(session)
        self._configs = UrgeConfigRepository(session)
        self._audit = AuditService(session)
        self._attachments = attachments or attachment_service

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    async def get_effective_config(self, tenant_id: UUID) -> dict[str, Any]:
        """取生效阈值。没有配置行就回退默认值，不抛错。

        不加缓存，与 ``WecomAlertConfig`` 的既有做法一致 —— 单行主键查询，
        而且阈值改了要立刻生效，缓存反而是麻烦。
        """
        cfg = await self._configs.get(tenant_id)
        if cfg is None:
            return dict(_FALLBACK)
        return {
            "no_publish_days": cfg.no_publish_days,
            "max_urge_times": cfg.max_urge_times,
            "max_overdue_days": cfg.max_overdue_days,
            "urge_threshold_days": cfg.urge_threshold_days,
            "important_threshold_days": cfg.important_threshold_days,
            "auto_scan_enabled": cfg.auto_scan_enabled,
        }

    async def get_urge_thresholds(self, tenant_id: UUID) -> UrgeThresholds:
        """urge_status 的两个分界天数。推广列表 / 详情、工作进度、汇总刷新都从这里取。"""
        cfg = await self.get_effective_config(tenant_id)
        return UrgeThresholds(
            urge_days=int(cfg["urge_threshold_days"]),
            important_days=int(cfg["important_threshold_days"]),
        )

    async def get_config_response(self, tenant_id: UUID) -> UrgeConfigResponse:
        return UrgeConfigResponse(**await self.get_effective_config(tenant_id))

    async def update_config(self, payload: UrgeConfigUpdate, user: User) -> UrgeConfigResponse:
        cfg: UrgeConfig = await self._configs.upsert(
            tenant_id=user.tenant_id,
            values={
                "no_publish_days": payload.no_publish_days,
                "max_urge_times": payload.max_urge_times,
                "max_overdue_days": payload.max_overdue_days,
                "urge_threshold_days": payload.urge_threshold_days,
                "important_threshold_days": payload.important_threshold_days,
                "auto_scan_enabled": payload.auto_scan_enabled,
            },
        )
        await self._audit.log(
            action="urge.config.update",
            resource="urge_config",
            resource_id=cfg.id,
            after={
                "no_publish_days": payload.no_publish_days,
                "max_urge_times": payload.max_urge_times,
                "max_overdue_days": payload.max_overdue_days,
                "urge_threshold_days": payload.urge_threshold_days,
                "important_threshold_days": payload.important_threshold_days,
                "auto_scan_enabled": payload.auto_scan_enabled,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return UrgeConfigResponse.model_validate(cfg)

    # ------------------------------------------------------------------ #
    # 列表 / 详情
    # ------------------------------------------------------------------ #

    async def list_tasks(
        self,
        filters: UrgeTaskListFilters,
        user: User,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[UrgeTaskResponse], int]:
        cfg = await self.get_effective_config(user.tenant_id)
        today = get_today()
        rows, total = await self._tasks.list_detailed(
            tenant_id=user.tenant_id,
            today=today,
            max_urge_times=cfg["max_urge_times"],
            status=filters.status.value if filters.status else None,
            pr_id=filters.pr_id,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            over_limit_only=filters.over_limit_only,
            overdue_only=filters.overdue_only,
            keyword=filters.keyword,
            page=page,
            page_size=page_size,
        )
        items = [self._row_to_task(r, max_urge_times=cfg["max_urge_times"]) for r in rows]
        return items, total

    async def get_task_detail(self, task_id: UUID, user: User) -> UrgeTaskDetailResponse:
        cfg = await self.get_effective_config(user.tenant_id)
        row = await self._tasks.get_detail(task_id, today=get_today())
        if row is None:
            raise UrgeTaskNotFoundError(f"催发任务 {task_id} 不存在")
        base = self._row_to_task(row, max_urge_times=cfg["max_urge_times"])
        records = await self._records.list_by_task(tenant_id=user.tenant_id, task_id=task_id)
        return UrgeTaskDetailResponse(
            **base.model_dump(),
            records=[self._row_to_record(r) for r in records],
        )

    async def get_records_for_promotion(
        self, promotion_id: UUID, user: User
    ) -> builtins.list[UrgeRecordResponse]:
        """推广单详情页用：直接按单据取催发时间线，不必先查任务。"""
        task = await self._tasks.get_by_promotion(
            tenant_id=user.tenant_id, promotion_id=promotion_id
        )
        if task is None:
            return []
        rows = await self._records.list_by_task(tenant_id=user.tenant_id, task_id=task.id)
        return [self._row_to_record(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 手动催发
    # ------------------------------------------------------------------ #

    async def urge_once(
        self,
        promotion_id: UUID,
        user: User,
        *,
        note: str | None = None,
        screenshot: tuple[str | None, str | None, bytes] | None = None,
    ) -> UrgeTaskDetailResponse:
        """手动催发单条。任务不存在就现建。

        ``screenshot`` 是 ``(filename, mime_type, data)``。后端代传而不是前端直传 R2 ——
        与收款码、款式主图一致，避免浏览器依赖 bucket CORS。

        上传失败要补偿删除 R2 对象（照 ``upload_payment_qr`` 的骨架），否则留下孤儿对象。
        """
        promo = await self._load_urgeable_promotion(promotion_id, user)
        task_id, _ = await self._tasks.ensure_task(
            tenant_id=user.tenant_id,
            promotion_id=promotion_id,
            blogger_id=promo["blogger_id"],
            pr_id=promo["pr_id"],
        )

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            if screenshot is not None:
                attachment_id, attachment_key = await self._store_screenshot(screenshot, user)

            bumped = await self._tasks.bump_urge_count(
                task_id=task_id, tenant_id=user.tenant_id, now=_utcnow()
            )
            if bumped is None:
                # 任务存在但已关闭。不静默成功 —— PR 得知道为什么催不出去。
                raise UrgeTaskClosedError(
                    "催发任务已关闭（博主已发布或单据已取消），无法继续催发",
                    details={"promotion_id": str(promotion_id), "task_id": str(task_id)},
                )

            self._records.add(
                UrgeRecord(
                    id=uuid4(),
                    tenant_id=user.tenant_id,
                    urge_task_id=task_id,
                    promotion_id=promotion_id,
                    trigger_type=UrgeTriggerType.MANUAL.value,
                    note=note,
                    screenshot_attachment_id=attachment_id,
                    created_by=user.id,
                )
            )
            await self._session.flush()
            await self._audit.log(
                action="urge.manual",
                resource="urge_task",
                resource_id=task_id,
                after={
                    "promotion_id": str(promotion_id),
                    "urge_count": bumped["urge_count"],
                    "has_screenshot": attachment_id is not None,
                },
                user_id=user.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            await self._compensate_screenshot(attachment_key, attachment_id)
            raise

        return await self.get_task_detail(task_id, user)

    async def urge_batch(
        self, style_id: UUID, user: User, *, note: str | None = None
    ) -> UrgeBatchResponse:
        """按款式批量催发。

        不支持截图：一次催十几个博主贴同一张截图没有留痕价值，要截图就逐条催。

        批量里单条失败不中断 —— 跳过并计数。一个博主的任务被关掉了不该让整批回滚。
        """
        candidates = await self._tasks.find_promotions_for_style(
            tenant_id=user.tenant_id, style_id=style_id
        )
        if not candidates:
            raise UrgeBatchEmptyError(
                "该款式下没有可催发的推广单（都已发布或已取消）",
                details={"style_id": str(style_id)},
            )

        now = _utcnow()
        task_ids: builtins.list[UUID] = []
        skipped = 0
        for row in candidates:
            task_id, _ = await self._tasks.ensure_task(
                tenant_id=user.tenant_id,
                promotion_id=row["promotion_id"],
                blogger_id=row["blogger_id"],
                pr_id=row["pr_id"],
            )
            bumped = await self._tasks.bump_urge_count(
                task_id=task_id, tenant_id=user.tenant_id, now=now
            )
            if bumped is None:
                skipped += 1
                continue
            self._records.add(
                UrgeRecord(
                    id=uuid4(),
                    tenant_id=user.tenant_id,
                    urge_task_id=task_id,
                    promotion_id=row["promotion_id"],
                    trigger_type=UrgeTriggerType.MANUAL.value,
                    note=note,
                    created_by=user.id,
                )
            )
            task_ids.append(task_id)

        await self._session.flush()
        await self._audit.log(
            action="urge.batch",
            resource="style",
            resource_id=style_id,
            after={"urged_count": len(task_ids), "skipped_closed": skipped},
            user_id=user.id,
        )
        await self._session.commit()
        return UrgeBatchResponse(
            style_id=style_id,
            urged_count=len(task_ids),
            task_ids=task_ids,
            skipped_closed=skipped,
        )

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #

    async def close_task(
        self, task_id: UUID, user: User, *, reason: str | None = None
    ) -> UrgeTaskDetailResponse:
        """主管手动关闭：已经决定走召回或转取消，不再催了。"""
        task = await self._tasks.get_by_id(task_id)
        if task is None:
            raise UrgeTaskNotFoundError(f"催发任务 {task_id} 不存在")

        closed = await self._tasks.close_task(
            task_id=task_id,
            tenant_id=user.tenant_id,
            reason=UrgeCloseReason.MANUAL.value,
            now=_utcnow(),
        )
        if closed is None:
            raise UrgeTaskClosedError("催发任务已关闭", details={"task_id": str(task_id)})

        # 关闭动作本身也写一条时间线，否则「为什么不催了」在详情页看不出来
        self._records.add(
            UrgeRecord(
                id=uuid4(),
                tenant_id=user.tenant_id,
                urge_task_id=task_id,
                promotion_id=task.promotion_id,
                trigger_type=UrgeTriggerType.MANUAL.value,
                note=f"[手动关闭] {reason}" if reason else "[手动关闭]",
                created_by=user.id,
            )
        )
        await self._session.flush()
        await self._audit.log(
            action="urge.close",
            resource="urge_task",
            resource_id=task_id,
            after={"close_reason": UrgeCloseReason.MANUAL.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get_task_detail(task_id, user)

    async def close_for_promotion(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
        reason: UrgeCloseReason,
    ) -> UUID | None:
        """推广单发布 / 取消时关掉催发任务（PRD「博主确认发布 → 任务自动关闭」）。

        **不 commit、不抛错**：由调用方（``PromotionService.publish`` / ``cancel``）
        在自己的事务里收尾。没有任务或已关闭都返回 None —— 一个压根没催过的单据
        不该因为「关不掉催发任务」而发布失败。
        """
        return await self._tasks.close_by_promotion(
            tenant_id=tenant_id,
            promotion_id=promotion_id,
            reason=reason.value,
            now=_utcnow(),
        )

    # ------------------------------------------------------------------ #
    # 自动扫描（Celery 调用）
    # ------------------------------------------------------------------ #

    async def scan_tenant(self, *, tenant_id: UUID, today: date) -> UrgeScanResult:
        """自动催发扫描。由 Celery Beat 逐租户调用，不走 HTTP 所以没有 User。

        幂等有两层：
        1. ``ensure_task`` 的 ``ON CONFLICT DO NOTHING`` —— 一单一任务
        2. ``bump_urge_count(auto_on=today)`` 的 ``last_auto_urged_on IS DISTINCT
           FROM :today`` —— 当天只自动催一次，Beat 重跑不会重复计次

        顺手收口陈旧任务：单据已发布/取消但任务还开着（历史数据或别的路径漏调用）。
        """
        result = UrgeScanResult()
        cfg = await self.get_effective_config(tenant_id)
        if not cfg["auto_scan_enabled"]:
            return result

        # 先收口再扫描：已发布的单据不该在本轮又被催一次
        for stale in await self._tasks.find_stale_open_tasks(tenant_id=tenant_id):
            reason = (
                UrgeCloseReason.PUBLISHED
                if stale["publish_status"] == "已发布"
                else UrgeCloseReason.CANCELLED
            )
            if (
                await self._tasks.close_task(
                    task_id=stale["task_id"],
                    tenant_id=tenant_id,
                    reason=reason.value,
                    now=_utcnow(),
                )
                is not None
            ):
                result.tasks_closed += 1

        candidates = await self._tasks.find_auto_scan_candidates(
            tenant_id=tenant_id,
            today=today,
            no_publish_days=cfg["no_publish_days"],
            max_overdue_days=cfg["max_overdue_days"],
        )
        now = _utcnow()
        for row in candidates:
            task_id, created = await self._tasks.ensure_task(
                tenant_id=tenant_id,
                promotion_id=row["promotion_id"],
                blogger_id=row["blogger_id"],
                pr_id=row["pr_id"],
            )
            if created:
                result.tasks_created += 1
            bumped = await self._tasks.bump_urge_count(
                task_id=task_id, tenant_id=tenant_id, now=now, auto_on=today
            )
            if bumped is None:
                # 当天已自动催过，或任务已关闭
                result.skipped_same_day += 1
                continue
            self._records.add(
                UrgeRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    urge_task_id=task_id,
                    promotion_id=row["promotion_id"],
                    trigger_type=UrgeTriggerType.AUTO.value,
                    note=f"距预定发布日 {(row['scheduled_publish_date'] - today).days} 天",
                )
            )
            result.records_created += 1

        await self._session.flush()
        return result

    # ------------------------------------------------------------------ #
    # 看板
    # ------------------------------------------------------------------ #

    async def dashboard(self, user: User) -> UrgeDashboardResponse:
        cfg = await self.get_effective_config(user.tenant_id)
        today = get_today()
        ws = _week_start(today)
        counts = await self._tasks.dashboard_counts(
            tenant_id=user.tenant_id,
            today=today,
            week_start=ws,
            max_urge_times=cfg["max_urge_times"],
        )
        return UrgeDashboardResponse(
            urged_this_week=int(counts["urged_this_week"] or 0),
            pending=int(counts["pending"] or 0),
            overdue=int(counts["overdue"] or 0),
            over_limit=int(counts["over_limit"] or 0),
            auto_scan_enabled=bool(cfg["auto_scan_enabled"]),
            max_urge_times=int(cfg["max_urge_times"]),
            week_start=ws,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    async def _load_urgeable_promotion(self, promotion_id: UUID, user: User) -> dict[str, Any]:
        """取推广单并校验「还能催」。

        刻意不复用 ``PromotionRepository.get_by_id`` —— 这里只要三个字段，
        而且要在同一条 SQL 里把可催性判掉。
        """
        from sqlalchemy import text as _text

        row = (
            await self._session.execute(
                _text(
                    """
                    SELECT id, blogger_id, pr_id, publish_status, is_active
                    FROM promotion
                    WHERE id = :pid AND tenant_id = :tid
                    """
                ),
                {"pid": promotion_id, "tid": user.tenant_id},
            )
        ).mappings()
        promo = row.first()
        if promo is None:
            raise UrgeTaskNotFoundError(f"推广 {promotion_id} 不存在")
        if not promo["is_active"] or promo["publish_status"] not in _URGEABLE_PUBLISH_STATUS:
            raise UrgeNotApplicableError(
                "只有未发布 / 异常状态的推广单可以催发",
                details={
                    "promotion_id": str(promotion_id),
                    "publish_status": promo["publish_status"],
                },
            )
        return dict(promo)

    async def _store_screenshot(
        self, screenshot: tuple[str | None, str | None, bytes], user: User
    ) -> tuple[UUID, str]:
        """校验 + 代传截图，返回 ``(attachment_id, r2_key)``。"""
        from fastapi.concurrency import run_in_threadpool

        filename, mime_type, data = screenshot
        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=_SCREENSHOT_MAX_BYTES,
            label="催发截图",
        )
        if problem is not None:
            raise UrgeScreenshotInvalidError(problem)

        attachment, _ = await self._attachments.create_upload_record(
            session=self._session,
            tenant_id=user.tenant_id,
            created_by=user.id,
            bucket="private",
            purpose=_SCREENSHOT_PURPOSE,
            filename=filename,
            mime_type=str(mime_type),
            size_bytes=len(data),
        )
        await run_in_threadpool(
            self._attachments.upload_bytes,
            data,
            bucket="private",
            key=attachment.r2_key,
            content_type=str(mime_type),
        )
        await self._attachments.mark_uploaded(
            session=self._session,
            attachment_id=attachment.id,
            tenant_id=user.tenant_id,
        )
        return attachment.id, attachment.r2_key

    async def _compensate_screenshot(
        self, attachment_key: str | None, attachment_id: UUID | None
    ) -> None:
        """事务回滚后删掉已经传上去的 R2 对象，不然留孤儿。"""
        if attachment_key is None:
            return
        from fastapi.concurrency import run_in_threadpool

        try:
            await run_in_threadpool(self._attachments.delete, "private", attachment_key)
        except Exception:
            log.warning(
                "urge_screenshot_compensation_delete_failed",
                extra={"attachment_id": str(attachment_id)},
            )

    def _row_to_task(self, row: Mapping[str, Any], *, max_urge_times: int) -> UrgeTaskResponse:
        overdue = row.get("overdue_days")
        return UrgeTaskResponse(
            id=row["id"],
            promotion_id=row["promotion_id"],
            promotion_internal_code=row.get("promotion_internal_code"),
            blogger_id=row["blogger_id"],
            blogger_nickname=row.get("blogger_nickname"),
            pr_id=row.get("pr_id"),
            pr_name=row.get("pr_name"),
            style_code=row.get("style_code"),
            style_name=row.get("style_name"),
            scheduled_publish_date=row.get("scheduled_publish_date"),
            publish_status=row.get("publish_status"),
            status=row["status"],
            urge_count=row["urge_count"],
            last_urged_at=row.get("last_urged_at"),
            closed_at=row.get("closed_at"),
            close_reason=row.get("close_reason"),
            # 「超过 N 次提示主管」。已关闭的不提示 —— 不用催了就没有召回的必要
            over_limit=(
                row["status"] == UrgeTaskStatus.OPEN.value and row["urge_count"] > max_urge_times
            ),
            overdue_days=int(overdue) if overdue is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_record(self, row: Mapping[str, Any]) -> UrgeRecordResponse:
        url: str | None = None
        key = row.get("screenshot_key")
        if key:
            try:
                url = self._attachments.get_signed_url("private", str(key), expires_in=3600)
            except Exception:
                # R2 没配置或签名失败不该让整条时间线打不开
                url = None
        return UrgeRecordResponse(
            id=row["id"],
            trigger_type=row["trigger_type"],
            note=row.get("note"),
            screenshot_url=url,
            wecom_message_id=row.get("wecom_message_id"),
            created_by=row.get("created_by"),
            created_by_name=row.get("created_by_name"),
            created_at=row["created_at"],
        )


__all__ = ["UrgeService"]
