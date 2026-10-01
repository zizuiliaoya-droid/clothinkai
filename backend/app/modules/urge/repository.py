"""催发任务仓储层。

列表与看板都要带推广单、博主、款式、PR 的字段，走 raw SQL 一次 JOIN 完。
状态写入用 ``UPDATE ... WHERE <旧状态> RETURNING`` 的乐观并发模式（与
``PromotionRepository.update_state`` 一致），不做读改写。
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.urge.enums import UrgeTaskStatus
from app.modules.urge.models import UrgeConfig, UrgeRecord, UrgeTask

# 列表与详情共用的投影，避免两处口径漂移
_SELECT_COLUMNS = """
    t.id, t.promotion_id, t.blogger_id, t.pr_id, t.status, t.urge_count,
    t.last_urged_at, t.closed_at, t.close_reason, t.created_at, t.updated_at,
    b.nickname AS blogger_nickname,
    COALESCE(pru.display_name, pru.username) AS pr_name,
    p.internal_code AS promotion_internal_code,
    p.style_code_snapshot AS style_code,
    p.style_short_name_snapshot AS style_name,
    p.scheduled_publish_date,
    p.publish_status,
    -- 超期天数：只有排了期且已过期才算，否则 NULL（前端显示「—」）
    --
    -- 这里必须写 CAST(:today AS date) 而不是 :today::date —— 同一个参数名在一条
    -- 语句里出现两次时，`::` 紧跟参数的那一处不会被替换，留下字面量 ":today"
    -- 直接语法错误。046/047 的 jsonb_build_object 栽的是同一个坑。
    CASE
      WHEN p.scheduled_publish_date IS NOT NULL
           AND p.scheduled_publish_date < :today
      THEN (CAST(:today AS date) - p.scheduled_publish_date)
      ELSE NULL
    END AS overdue_days
"""

_JOINS = """
    FROM urge_task t
    JOIN promotion p ON p.id = t.promotion_id
    LEFT JOIN blogger b ON b.id = t.blogger_id
    LEFT JOIN "user" pru ON pru.id = t.pr_id
"""


class UrgeConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID) -> UrgeConfig | None:
        # 用 ORM select 而不是 text()：text().columns() 配 scalar_one_or_none()
        # 拿到的是第一列（id）而不是实体，静默返回一个 UUID。
        stmt = select(UrgeConfig).where(UrgeConfig.tenant_id == tenant_id).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, *, tenant_id: UUID, values: dict[str, Any]) -> UrgeConfig:
        """单语句 upsert（照 ``AlertConfigService.upsert`` 的做法）。"""
        stmt = (
            pg_insert(UrgeConfig)
            .values(id=uuid4(), tenant_id=tenant_id, **values)
            .on_conflict_do_update(
                index_elements=["tenant_id"],
                set_={**values, "updated_at": text("now()")},
            )
            .returning(UrgeConfig)
        )
        return (await self._session.execute(stmt)).scalar_one()


class UrgeTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get ----------------------- #

    async def get_by_id(self, task_id: UUID) -> UrgeTask | None:
        return await self._session.get(UrgeTask, task_id)

    async def get_detail(self, task_id: UUID, *, today: date) -> Mapping[str, Any] | None:
        rows = (
            await self._session.execute(
                text(f"SELECT {_SELECT_COLUMNS} {_JOINS} WHERE t.id = :tid"),
                {"tid": task_id, "today": today},
            )
        ).mappings()
        first = rows.first()
        return dict(first) if first is not None else None

    async def get_by_promotion(self, *, tenant_id: UUID, promotion_id: UUID) -> UrgeTask | None:
        stmt = (
            select(UrgeTask)
            .where(UrgeTask.tenant_id == tenant_id, UrgeTask.promotion_id == promotion_id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ----------------------- 建任务 ----------------------- #

    async def ensure_task(
        self,
        *,
        tenant_id: UUID,
        promotion_id: UUID,
        blogger_id: UUID,
        pr_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """取或建催发任务，返回 ``(task_id, created)``。

        靠 ``uq_urge_task_promotion`` 做幂等，不用 SELECT-then-INSERT ——
        自动扫描是多实例环境，那个模式真并发会建出两条
        （``wecom/scan_service.exists_today_non_failed`` 就是这个问题）。

        ``DO NOTHING`` 在冲突时不返回行，所以冲突后补一次 SELECT 拿已有 id。
        """
        new_id = uuid4()
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO urge_task
                      (id, tenant_id, promotion_id, blogger_id, pr_id,
                       status, urge_count, created_at, updated_at)
                    VALUES
                      (:id, :tenant_id, :promotion_id, :blogger_id, :pr_id,
                       :status, 0, NOW(), NOW())
                    ON CONFLICT (tenant_id, promotion_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "promotion_id": promotion_id,
                    "blogger_id": blogger_id,
                    "pr_id": pr_id,
                    "status": UrgeTaskStatus.OPEN.value,
                },
            )
        ).first()
        if row is not None:
            return new_id, True

        existing = (
            await self._session.execute(
                text("SELECT id FROM urge_task WHERE tenant_id = :t AND promotion_id = :p"),
                {"t": tenant_id, "p": promotion_id},
            )
        ).scalar_one()
        return UUID(str(existing)), False

    # ----------------------- 催发计次 ----------------------- #

    async def bump_urge_count(
        self,
        *,
        task_id: UUID,
        tenant_id: UUID,
        now: datetime,
        auto_on: date | None = None,
    ) -> Mapping[str, Any] | None:
        """累加催发次数。只对「进行中」的任务生效。

        ``auto_on`` 传值时额外要求 ``last_auto_urged_on`` 不等于它 —— 自动扫描的
        当日幂等就靠这个条件，单语句原子，0 行就是「今天已经自动催过了」。
        手动催发不受当日限制（PR 想一天催两次是他的事）。
        """
        clauses = [
            "id = :task_id",
            "tenant_id = :tenant_id",
            "status = :open",
        ]
        params: dict[str, Any] = {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "open": UrgeTaskStatus.OPEN.value,
            "now": now,
        }
        if auto_on is not None:
            clauses.append("last_auto_urged_on IS DISTINCT FROM :auto_on")
            params["auto_on"] = auto_on
            set_auto = ", last_auto_urged_on = :auto_on"
        else:
            set_auto = ""

        row = (
            await self._session.execute(
                text(
                    f"""
                    UPDATE urge_task
                    SET urge_count = urge_count + 1,
                        last_urged_at = :now,
                        updated_at = NOW()
                        {set_auto}
                    WHERE {" AND ".join(clauses)}
                    RETURNING id, urge_count, status
                    """
                ),
                params,
            )
        ).mappings()
        first = row.first()
        return dict(first) if first is not None else None

    # ----------------------- 关闭 ----------------------- #

    async def close_task(
        self,
        *,
        task_id: UUID,
        tenant_id: UUID,
        reason: str,
        now: datetime,
    ) -> UUID | None:
        """关闭任务。已关闭的返回 None（幂等，重复关闭不报错）。"""
        return (
            await self._session.execute(
                text(
                    """
                    UPDATE urge_task
                    SET status = :closed, closed_at = :now,
                        close_reason = :reason, updated_at = NOW()
                    WHERE id = :task_id AND tenant_id = :tenant_id AND status = :open
                    RETURNING id
                    """
                ),
                {
                    "task_id": task_id,
                    "tenant_id": tenant_id,
                    "closed": UrgeTaskStatus.CLOSED.value,
                    "open": UrgeTaskStatus.OPEN.value,
                    "reason": reason,
                    "now": now,
                },
            )
        ).scalar_one_or_none()

    async def close_by_promotion(
        self,
        *,
        tenant_id: UUID,
        promotion_id: UUID,
        reason: str,
        now: datetime,
    ) -> UUID | None:
        """按推广单关闭进行中的任务（发布 / 取消时调用）。

        没有任务或已关闭都返回 None —— 调用方（publish / cancel）不该因为
        「这单压根没建过催发任务」而失败。
        """
        return (
            await self._session.execute(
                text(
                    """
                    UPDATE urge_task
                    SET status = :closed, closed_at = :now,
                        close_reason = :reason, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND promotion_id = :promotion_id
                      AND status = :open
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "promotion_id": promotion_id,
                    "closed": UrgeTaskStatus.CLOSED.value,
                    "open": UrgeTaskStatus.OPEN.value,
                    "reason": reason,
                    "now": now,
                },
            )
        ).scalar_one_or_none()

    # ----------------------- list ----------------------- #

    async def list_detailed(
        self,
        *,
        tenant_id: UUID,
        today: date,
        max_urge_times: int,
        status: str | None = None,
        pr_id: UUID | None = None,
        blogger_id: UUID | None = None,
        style_id: UUID | None = None,
        over_limit_only: bool = False,
        overdue_only: bool = False,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[Mapping[str, Any]], int]:
        clauses = ["t.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "today": today}
        if status:
            clauses.append("t.status = :status")
            params["status"] = status
        if pr_id is not None:
            clauses.append("t.pr_id = :pr_id")
            params["pr_id"] = pr_id
        if blogger_id is not None:
            clauses.append("t.blogger_id = :blogger_id")
            params["blogger_id"] = blogger_id
        if style_id is not None:
            clauses.append("p.style_id = :style_id")
            params["style_id"] = style_id
        if over_limit_only:
            clauses.append("t.urge_count > :max_urge_times")
            params["max_urge_times"] = max_urge_times
        if overdue_only:
            clauses.append(
                "p.scheduled_publish_date IS NOT NULL AND p.scheduled_publish_date < :today"
            )
        if keyword:
            clauses.append(
                "(b.nickname ILIKE :kw OR p.internal_code ILIKE :kw"
                " OR p.style_code_snapshot ILIKE :kw)"
            )
            params["kw"] = f"%{keyword}%"
        where = " AND ".join(clauses)

        total = int(
            (
                await self._session.execute(text(f"SELECT COUNT(*) {_JOINS} WHERE {where}"), params)
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    {_JOINS}
                    WHERE {where}
                    ORDER BY
                      -- 进行中排前面，然后越超期越靠前：主管打开就看到最该处理的
                      CASE t.status WHEN '进行中' THEN 0 ELSE 1 END,
                      p.scheduled_publish_date ASC NULLS LAST,
                      t.created_at DESC
                    OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(r) for r in rows], total

    # ----------------------- 批量候选 ----------------------- #

    async def find_promotions_for_style(
        self, *, tenant_id: UUID, style_id: UUID
    ) -> builtins.list[Mapping[str, Any]]:
        """按款式找可催的推广单（未发布 / 异常）。

        「可催」不看有没有任务 —— 没有就现建。已取消/已发布/已删除的排除掉。
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT p.id AS promotion_id, p.blogger_id, p.pr_id
                    FROM promotion p
                    WHERE p.tenant_id = :tenant_id
                      AND p.style_id = :style_id
                      AND p.is_active = true
                      AND p.publish_status IN ('未发布', '异常')
                    ORDER BY p.scheduled_publish_date ASC NULLS LAST
                    """
                ),
                {"tenant_id": tenant_id, "style_id": style_id},
            )
        ).mappings()
        return [dict(r) for r in rows]

    async def find_auto_scan_candidates(
        self,
        *,
        tenant_id: UUID,
        today: date,
        no_publish_days: int,
        max_overdue_days: int,
    ) -> builtins.list[Mapping[str, Any]]:
        """自动扫描候选。

        窗口是 ``[today - max_overdue_days, today + no_publish_days]``：
        - 上界 = PRD「距预定发布日 ≤ N 天未发布」
        - 下界 = 防炸。生产有 5134 条历史单排期在半年前，不设下界的话第一次扫描
          就建 5134 个任务，而且之后天天催。这些单仍可手动催。

        刻意不复用 ``PromotionRepository.find_urge_candidates`` —— 那个按
        urge_status 取「催发/重要催发/超时」，超时无下界，正是要避免的。
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT p.id AS promotion_id, p.blogger_id, p.pr_id,
                           p.scheduled_publish_date
                    FROM promotion p
                    WHERE p.tenant_id = :tenant_id
                      AND p.is_active = true
                      AND p.publish_status IN ('未发布', '异常')
                      AND p.scheduled_publish_date IS NOT NULL
                      AND p.scheduled_publish_date <= :upper
                      AND p.scheduled_publish_date >= :lower
                    ORDER BY p.scheduled_publish_date ASC
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "upper": today + timedelta(days=no_publish_days),
                    "lower": today - timedelta(days=max_overdue_days),
                },
            )
        ).mappings()
        return [dict(r) for r in rows]

    async def find_stale_open_tasks(self, *, tenant_id: UUID) -> builtins.list[Mapping[str, Any]]:
        """任务还开着但推广单已经发布 / 取消 / 删除了。

        正常路径下 publish / cancel 会同事务关任务，这里是兜底：历史数据、
        Excel 导入改状态、或者将来有别的路径漏了调用，都靠扫描收口。
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT t.id AS task_id, p.publish_status
                    FROM urge_task t
                    JOIN promotion p ON p.id = t.promotion_id
                    WHERE t.tenant_id = :tenant_id
                      AND t.status = :open
                      AND (p.publish_status NOT IN ('未发布', '异常') OR p.is_active = false)
                    """
                ),
                {"tenant_id": tenant_id, "open": UrgeTaskStatus.OPEN.value},
            )
        ).mappings()
        return [dict(r) for r in rows]

    # ----------------------- 看板 ----------------------- #

    async def dashboard_counts(
        self, *, tenant_id: UUID, today: date, week_start: date, max_urge_times: int
    ) -> Mapping[str, Any]:
        """一条 SQL 出全部看板数字，避免四次往返。"""
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM urge_record r
                        WHERE r.tenant_id = :tenant_id
                          AND (r.created_at AT TIME ZONE 'Asia/Shanghai')::date
                              >= :week_start) AS urged_this_week,
                      COUNT(*) FILTER (WHERE t.status = :open) AS pending,
                      COUNT(*) FILTER (
                        WHERE t.status = :open
                          AND p.scheduled_publish_date IS NOT NULL
                          AND p.scheduled_publish_date < :today
                      ) AS overdue,
                      COUNT(*) FILTER (
                        WHERE t.status = :open AND t.urge_count > :max_urge_times
                      ) AS over_limit
                    FROM urge_task t
                    JOIN promotion p ON p.id = t.promotion_id
                    WHERE t.tenant_id = :tenant_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "today": today,
                    "week_start": week_start,
                    "max_urge_times": max_urge_times,
                    "open": UrgeTaskStatus.OPEN.value,
                },
            )
        ).mappings()
        first = row.first()
        # 没有任何任务时 FILTER 聚合返回 0，但 urged_this_week 的子查询照样有值；
        # 空表场景 first 仍非 None（聚合查询总返回一行），这里只兜极端情况
        return (
            dict(first)
            if first is not None
            else {"urged_this_week": 0, "pending": 0, "overdue": 0, "over_limit": 0}
        )


class UrgeRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, record: UrgeRecord) -> None:
        self._session.add(record)

    async def list_by_task(
        self, *, tenant_id: UUID, task_id: UUID, limit: int = 100
    ) -> Sequence[Mapping[str, Any]]:
        """任务的催发时间线，倒序。"""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT r.id, r.trigger_type, r.note, r.wecom_message_id,
                           r.created_by, r.created_at,
                           a.r2_key AS screenshot_key,
                           COALESCE(u.display_name, u.username) AS created_by_name
                    FROM urge_record r
                    LEFT JOIN attachment a ON a.id = r.screenshot_attachment_id
                    LEFT JOIN "user" u ON u.id = r.created_by
                    WHERE r.tenant_id = :tenant_id AND r.urge_task_id = :task_id
                    ORDER BY r.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "task_id": task_id, "limit": limit},
            )
        ).mappings()
        return [dict(r) for r in rows]


__all__ = ["UrgeConfigRepository", "UrgeRecordRepository", "UrgeTaskRepository"]
