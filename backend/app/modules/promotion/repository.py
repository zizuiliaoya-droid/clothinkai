"""U04 promotion 仓储层（PromotionRepository）。

按 nfr-design/logical-components.md §4.2 + nfr-design-patterns.md P-U04-01/P-U04-03/P-U04-04：
- DB 操作（CRUD + 复杂查询）
- 不写业务规则
- 自动应用 RLS（依赖 Session 注入 tenant_id）

3 个关键方法：
- ``next_internal_sequence``：FB2 修正 — INSERT ON CONFLICT DO UPDATE RETURNING（首次创建无 race）
- ``update_state``：FB7 修正 — UPDATE WHERE old_state + tenant_id + is_active RETURNING（乐观并发）
- ``list_with_cte``：CTE 注入 urge_status / dual_platform 计算列（FB8 — :today 参数化）
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import promotion_sequence_lock_duration_seconds
from app.modules.promotion.exceptions import SequenceOverflowError
from app.modules.promotion.models import Promotion, PromotionSequence
from app.modules.promotion.urge_calculator import URGE_STATUS_SQL_EXPR


# ---------------------------------------------------------------------------
# Filters dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionListFilters:
    """列表查询筛选条件（service 层从 PromotionListFilters Pydantic 转换而来）。"""

    keyword: str | None = None
    publish_status: str | None = None
    recall_status: str | None = None
    settlement_status: str | None = None
    platform: str | None = None
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    pr_id: UUID | None = None
    cooperation_date_from: date | None = None
    cooperation_date_to: date | None = None
    scheduled_publish_date_from: date | None = None
    scheduled_publish_date_to: date | None = None
    is_active: bool | None = True
    only_dual_platform: bool = False
    is_hit: bool | None = None
    hit_threshold: int = 1000


@dataclass(frozen=True)
class PromotionListRow:
    """list_with_cte 返回的轻量行（含计算字段）。"""

    promotion: Promotion
    urge_status: str
    dual_platform: bool


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PromotionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / count ----------------------- #

    async def get_by_id(
        self, promotion_id: UUID, *, include_inactive: bool = False
    ) -> Promotion | None:
        promotion = await self._session.get(Promotion, promotion_id)
        if promotion is None:
            return None
        if not promotion.is_active and not include_inactive:
            return None
        return promotion

    async def get_by_internal_code(
        self, internal_code: str
    ) -> Promotion | None:
        stmt = (
            select(Promotion)
            .where(
                Promotion.internal_code == internal_code,
                Promotion.is_active.is_(True),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_urge_candidates(
        self,
        *,
        today: date,
        urge_days: int = 10,
        important_days: int = 3,
    ) -> list[Any]:
        """U07 EP08-S05：返回需催发的推广候选（urge_status ∈ 催发/重要催发/超时）。

        复用 ``URGE_STATUS_SQL_EXPR``（FB8 :today 参数化）+ JOIN blogger 取昵称。
        返回 RowMapping 列表（键：promotion_id / blogger_id / pr_id /
        scheduled_publish_date / publish_status / style_short_name_snapshot /
        blogger_nickname）。RLS 自动按 tenant 过滤。
        """
        stmt = text(
            f"""
            SELECT
                promotion.id AS promotion_id,
                promotion.blogger_id AS blogger_id,
                promotion.pr_id AS pr_id,
                promotion.scheduled_publish_date AS scheduled_publish_date,
                promotion.publish_status AS publish_status,
                promotion.style_short_name_snapshot AS style_short_name_snapshot,
                blogger.nickname AS blogger_nickname
            FROM promotion
            JOIN blogger ON blogger.id = promotion.blogger_id
            WHERE promotion.is_active = true
              AND promotion.publish_status IN ('未发布', '异常')
              AND promotion.scheduled_publish_date IS NOT NULL
              AND ({URGE_STATUS_SQL_EXPR}) IN ('催发', '重要催发', '超时')
            ORDER BY promotion.scheduled_publish_date ASC
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "today": today,
                "urge_days": urge_days,
                "important_days": important_days,
            },
        )
        return list(result.mappings().all())

    async def find_active_duplicate(
        self,
        *,
        style_id: UUID,
        blogger_id: UUID,
        exclude_id: UUID | None = None,
    ) -> Sequence[Promotion]:
        """重复检测（EP05-S04）：同 style_id + blogger_id 的"活跃"推广。

        活跃定义：``publish_status NOT IN ('已取消', '已删除')`` 且 ``is_active = true``。
        返回所有重复（非阻塞 warning，由 service 层包装为 PromotionDuplicateWarning）。
        """
        stmt = (
            select(Promotion)
            .where(
                Promotion.style_id == style_id,
                Promotion.blogger_id == blogger_id,
                Promotion.is_active.is_(True),
                Promotion.publish_status.notin_(("已取消", "已删除")),
            )
            .order_by(Promotion.created_at.desc())
            .limit(10)
        )
        if exclude_id is not None:
            stmt = stmt.where(Promotion.id != exclude_id)
        return (await self._session.execute(stmt)).scalars().all()

    async def has_other_platforms_for_style(
        self,
        *,
        style_id: UUID,
        platform: str,
        exclude_id: UUID | None = None,
    ) -> bool:
        """dual_platform 计算（EP05-S05）：同 style_id 是否有其他平台的活跃 promotion。

        返回 True 表示该 style 已在其他平台有推广，前端展示 dual_platform 标记。
        """
        stmt = select(
            exists().where(
                Promotion.style_id == style_id,
                Promotion.platform != platform,
                Promotion.is_active.is_(True),
                Promotion.publish_status.notin_(("已取消", "已删除")),
            )
        )
        if exclude_id is not None:
            stmt = select(
                exists().where(
                    Promotion.style_id == style_id,
                    Promotion.platform != platform,
                    Promotion.is_active.is_(True),
                    Promotion.publish_status.notin_(("已取消", "已删除")),
                    Promotion.id != exclude_id,
                )
            )
        result = await self._session.execute(stmt)
        return bool(result.scalar_one())

    # ----------------------- write ----------------------- #

    def add(self, promotion: Promotion) -> None:
        self._session.add(promotion)


    # ----------------------- next_internal_sequence (FB2) ----------------------- #

    async def next_internal_sequence(
        self,
        *,
        tenant_id: UUID,
        date_key: date,
    ) -> int:
        """原子获取下一序列号。

        FB2 修正：单条 ``INSERT ... ON CONFLICT DO UPDATE RETURNING`` 保证：
        - 首次创建（行不存在）和后续 UPDATE 走同一路径，无 race window
        - PostgreSQL 唯一索引保证只一个 INSERT 成功，其余走 DO UPDATE
        - 单语句原子，无需 SELECT FOR UPDATE 悲观锁

        监控：``promotion_sequence_lock_duration_seconds`` Histogram。

        Raises:
            SequenceOverflowError: 当天序号超过 9999。
        """
        start = time.perf_counter()
        try:
            stmt = text(
                """
                INSERT INTO promotion_sequence
                    (id, tenant_id, date_key, last_seq, created_at, updated_at)
                VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
                ON CONFLICT (tenant_id, date_key) DO UPDATE
                SET last_seq = promotion_sequence.last_seq + 1,
                    updated_at = NOW()
                RETURNING last_seq
                """
            )
            result = await self._session.execute(
                stmt, {"tid": tenant_id, "dk": date_key}
            )
            next_seq = int(result.scalar_one())
        finally:
            promotion_sequence_lock_duration_seconds.observe(
                time.perf_counter() - start
            )

        if next_seq > 9999:
            raise SequenceOverflowError(
                f"当天序号已达 {next_seq}，超出 9999 上限",
                details={"tenant_id": str(tenant_id), "date_key": str(date_key)},
            )
        return next_seq

    # ----------------------- update_state (FB7) ----------------------- #

    async def update_state(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
        from_state_field: str,
        from_state_value: str,
        to_state_value: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> Promotion | None:
        """乐观并发 UPDATE WHERE old_state RETURNING（FB7 强化）。

        WHERE 条件包含：
        - ``id = :promotion_id``
        - ``tenant_id = :tenant_id``（多租户防护，与 RLS 双保险）
        - ``is_active = true``（软删除防护）
        - ``<state_field> = :from_state_value``（旧状态防护）

        Returns:
            ``Promotion`` 实例（推进成功）；
            ``None`` 表示并发冲突 / 已被推进 / 软删除 / 跨租户 — service 层应抛
            ``StateTransitionConflictError``。

        Args:
            from_state_field: ``"publish_status"`` / ``"recall_status"`` / ``"settlement_status"``
            extra_fields: 状态推进时一并写入的字段（如 publish 时的 publish_url、
                actual_publish_date；review 时的 reviewed_by、reviewed_at 等）。
        """
        if from_state_field not in {
            "publish_status",
            "recall_status",
            "settlement_status",
        }:
            raise ValueError(
                f"unsupported state field: {from_state_field}"
            )

        state_col = getattr(Promotion, from_state_field)
        values: dict[str, Any] = dict(extra_fields or {})
        values[from_state_field] = to_state_value
        values["updated_at"] = func.now()

        stmt = (
            update(Promotion)
            .where(
                Promotion.id == promotion_id,
                Promotion.tenant_id == tenant_id,
                Promotion.is_active.is_(True),
                state_col == from_state_value,
            )
            .values(**values)
            .returning(Promotion)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None
        # SQLAlchemy 2.0: returning(Model) 可能命中 session 身份映射中的旧实例
        # （状态字段未同步）；refresh 以反映 DB 最新状态。
        promotion: Promotion = row[0]
        await self._session.refresh(promotion)
        return promotion

    # ----------------------- soft delete / restore ----------------------- #

    async def soft_deactivate(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
    ) -> Promotion | None:
        """is_active=false（与状态机正交的软停用，BR-U04 通用删除路径之一）。"""
        stmt = (
            update(Promotion)
            .where(
                Promotion.id == promotion_id,
                Promotion.tenant_id == tenant_id,
                Promotion.is_active.is_(True),
            )
            .values(is_active=False, updated_at=func.now())
            .returning(Promotion)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None

    async def update_like_count(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
        like_count: int,
    ) -> Promotion | None:
        """U13 数据采集 Worker 内部调用：更新 like_count。

        WHERE 包含 tenant_id + is_active 防护。
        """
        stmt = (
            update(Promotion)
            .where(
                Promotion.id == promotion_id,
                Promotion.tenant_id == tenant_id,
                Promotion.is_active.is_(True),
            )
            .values(like_count=like_count, updated_at=func.now())
            .returning(Promotion)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None


    # ----------------------- list_with_cte (FB8 + Pattern P-U04-04) ----------------------- #

    async def list_with_cte(
        self,
        *,
        tenant_id: UUID,
        filters: PromotionListFilters,
        page: int,
        page_size: int,
        today: date,
        urge_threshold_days: int,
        important_threshold_days: int,
    ) -> tuple[list[PromotionListRow], int]:
        """列表查询，CTE 注入 ``urge_status`` / ``dual_platform`` 计算列。

        关键点（FB8）：
        - ``today`` 由 service 层 ``get_today()`` 注入；SQL 不用 ``CURRENT_DATE``
        - ``urge_threshold_days`` / ``important_threshold_days`` 由 service 注入

        Returns:
            ``(rows, total)`` — rows 含 promotion + urge_status + dual_platform。
        """
        # ------ 构造 base CTE（含计算列）------
        base_sql = f"""
        WITH base AS (
            SELECT p.*,
                   {URGE_STATUS_SQL_EXPR.strip()} AS urge_status,
                   EXISTS (
                       SELECT 1 FROM promotion p2
                       WHERE p2.tenant_id = p.tenant_id
                         AND p2.style_id = p.style_id
                         AND p2.platform <> p.platform
                         AND p2.is_active = true
                         AND p2.publish_status NOT IN ('已取消', '已删除')
                         AND p2.id <> p.id
                   ) AS dual_platform_calc
            FROM promotion p
            WHERE p.tenant_id = :tenant_id
        )
        SELECT * FROM base WHERE 1=1
        """
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "today": today,
            "urge_days": urge_threshold_days,
            "important_days": important_threshold_days,
        }

        # ------ 动态 WHERE ------
        clauses: list[str] = []
        if filters.is_active is not None:
            clauses.append("is_active = :is_active")
            params["is_active"] = filters.is_active
        if filters.publish_status:
            clauses.append("publish_status = :publish_status")
            params["publish_status"] = filters.publish_status
        if filters.recall_status:
            clauses.append("recall_status = :recall_status")
            params["recall_status"] = filters.recall_status
        if filters.settlement_status:
            clauses.append("settlement_status = :settlement_status")
            params["settlement_status"] = filters.settlement_status
        if filters.platform:
            clauses.append("platform = :platform")
            params["platform"] = filters.platform
        if filters.blogger_id:
            clauses.append("blogger_id = :blogger_id")
            params["blogger_id"] = filters.blogger_id
        if filters.style_id:
            clauses.append("style_id = :style_id")
            params["style_id"] = filters.style_id
        if filters.pr_id:
            clauses.append("pr_id = :pr_id")
            params["pr_id"] = filters.pr_id
        if filters.cooperation_date_from:
            clauses.append("cooperation_date >= :coop_from")
            params["coop_from"] = filters.cooperation_date_from
        if filters.cooperation_date_to:
            clauses.append("cooperation_date <= :coop_to")
            params["coop_to"] = filters.cooperation_date_to
        if filters.scheduled_publish_date_from:
            clauses.append("scheduled_publish_date >= :sched_from")
            params["sched_from"] = filters.scheduled_publish_date_from
        if filters.scheduled_publish_date_to:
            clauses.append("scheduled_publish_date <= :sched_to")
            params["sched_to"] = filters.scheduled_publish_date_to
        if filters.only_dual_platform:
            clauses.append("dual_platform_calc = true")
        if filters.is_hit is True:
            clauses.append("(like_count IS NOT NULL AND like_count >= :hit_th)")
            params["hit_th"] = filters.hit_threshold
        elif filters.is_hit is False:
            clauses.append(
                "(like_count IS NULL OR like_count < :hit_th)"
            )
            params["hit_th"] = filters.hit_threshold
        if filters.keyword:
            # 命中 GIN trgm 索引（idx_promotion_internal_code_trgm 等）
            clauses.append(
                "(internal_code ILIKE :kw "
                "OR style_code_snapshot ILIKE :kw "
                "OR style_short_name_snapshot ILIKE :kw)"
            )
            params["kw"] = f"%{filters.keyword}%"

        where_extra = ""
        if clauses:
            where_extra = " AND " + " AND ".join(clauses)

        # ------ 1. count ------
        count_sql = (
            f"SELECT COUNT(*) FROM ({base_sql}{where_extra}) AS c"
        )
        total = int(
            (await self._session.execute(text(count_sql), params)).scalar_one()
        )

        # ------ 2. data ------
        data_sql = (
            base_sql
            + where_extra
            + " ORDER BY cooperation_date DESC, created_at DESC "
            + " LIMIT :limit OFFSET :offset"
        )
        params["limit"] = page_size
        params["offset"] = (page - 1) * page_size

        result = await self._session.execute(text(data_sql), params)
        rows: list[PromotionListRow] = []

        # 将 raw row 重组为 ORM 实例（共享同一 session）
        # 注意：ORM 重组时 do_orm_execute 不触发，需要手动构造
        for row in result.mappings().all():
            promotion = Promotion(**{
                col: row[col] for col in (
                    "id", "tenant_id", "style_id", "sku_id", "blogger_id",
                    "pr_id", "internal_code", "style_code_snapshot",
                    "style_short_name_snapshot", "quote_amount",
                    "cost_snapshot", "platform", "cooperation_date",
                    "scheduled_publish_date", "actual_publish_date",
                    "publish_url", "cancel_reason", "recall_reason",
                    "like_count", "note_title", "remark",
                    "publish_status", "recall_status", "settlement_status",
                    "reviewed_by", "reviewed_at", "review_action",
                    "review_reason", "is_active", "created_at", "updated_at",
                ) if col in row
            })
            # 防止重组的 ORM 实例污染 session unit of work
            if promotion in self._session:
                self._session.expunge(promotion)
            rows.append(
                PromotionListRow(
                    promotion=promotion,
                    urge_status=row["urge_status"],
                    dual_platform=bool(row["dual_platform_calc"]),
                )
            )
        return rows, total


__all__ = [
    "PromotionListFilters",
    "PromotionListRow",
    "PromotionRepository",
]
