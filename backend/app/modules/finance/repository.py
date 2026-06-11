"""U05 finance 仓储层（SettlementRepository）。

按 nfr-design/logical-components.md §4.2 + nfr-design-patterns.md P-U05-01/03/04：
- DB 操作（CRUD + 复杂查询）
- 不写业务规则
- 自动应用 RLS（依赖 Session 注入 tenant_id）

3 个关键方法（与 U04 模式完全一致）：
- ``next_settlement_sequence``：复用 U04 FB2 INSERT ON CONFLICT DO UPDATE RETURNING
- ``update_state``：复用 U04 FB7 UPDATE WHERE 旧 state RETURNING（**无 is_active 字段，FB3**）
- ``daily_summary_as_of`` / ``daily_summary_activity``：FB7 双口径汇总

序列号 SequenceOverflowError 抛出时机：超过 9999。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import settlement_sequence_lock_duration_seconds
from app.modules.finance.exceptions import SequenceOverflowError
from app.modules.finance.models import (
    Settlement,
    SettlementExtraItem,
    SettlementSequence,
)


# ---------------------------------------------------------------------------
# Filters dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementListFilters:
    """列表查询筛选条件（service 层从 SettlementListFilters Pydantic 转换而来）."""

    keyword: str | None = None
    settlement_status: str | None = None
    promotion_id: UUID | None = None
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    pr_id: UUID | None = None
    reviewed_by: UUID | None = None
    paid_by: UUID | None = None
    created_at_from: date | None = None
    created_at_to: date | None = None
    payment_date_from: date | None = None
    payment_date_to: date | None = None
    amount_from: Decimal | None = None
    amount_to: Decimal | None = None
    payment_amount_from: Decimal | None = None
    payment_amount_to: Decimal | None = None
    is_my_only: bool = False  # PR 角色限自己提交（service 层强制注入 pr_id 过滤）


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SettlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / find ----------------------- #

    async def get_by_id(
        self, settlement_id: UUID
    ) -> Settlement | None:
        """无 is_active 字段（FB3），任何 settlement 都是活跃的。"""
        return await self._session.get(Settlement, settlement_id)

    async def get_by_settlement_no(
        self, settlement_no: str
    ) -> Settlement | None:
        stmt = select(Settlement).where(
            Settlement.settlement_no == settlement_no
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_by_promotion_id(
        self, promotion_id: UUID
    ) -> Settlement | None:
        """幂等 SELECT 兜底（与 DB UNIQUE 永久 + UNIQUE(request_event_id) 三重防护，FB1+FB3）。"""
        stmt = select(Settlement).where(Settlement.promotion_id == promotion_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_by_request_event_id(
        self, request_event_id: UUID
    ) -> Settlement | None:
        """事件重放兜底 SELECT（DB UNIQUE 已防，service 层 SELECT 友好 audit 区分）。"""
        stmt = select(Settlement).where(
            Settlement.request_event_id == request_event_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ----------------------- write helper ----------------------- #

    def add(self, settlement: Settlement) -> None:
        self._session.add(settlement)


    # ----------------------- next_settlement_sequence (FB2 复用 U04) ----------------------- #

    async def next_settlement_sequence(
        self,
        *,
        tenant_id: UUID,
        date_key: date,
    ) -> int:
        """原子获取下一序列号（与 U04 PromotionSequence 完全一致模式）.

        FB2 修正：单条 ``INSERT ... ON CONFLICT DO UPDATE RETURNING`` 保证：
        - 首次创建（行不存在）和后续 UPDATE 走同一路径，无 race window
        - PostgreSQL 唯一索引保证只一个 INSERT 成功，其余走 DO UPDATE
        - 单语句原子，无需 SELECT FOR UPDATE 悲观锁

        监控：``settlement_sequence_lock_duration_seconds`` Histogram。

        Raises:
            SequenceOverflowError: 当天序号超过 9999。
        """
        start = time.perf_counter()
        try:
            stmt = text(
                """
                INSERT INTO settlement_sequence
                    (id, tenant_id, date_key, last_seq, created_at, updated_at)
                VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
                ON CONFLICT (tenant_id, date_key) DO UPDATE
                SET last_seq = settlement_sequence.last_seq + 1,
                    updated_at = NOW()
                RETURNING last_seq
                """
            )
            result = await self._session.execute(
                stmt, {"tid": tenant_id, "dk": date_key}
            )
            next_seq = int(result.scalar_one())
        finally:
            settlement_sequence_lock_duration_seconds.observe(
                time.perf_counter() - start
            )

        if next_seq > 9999:
            raise SequenceOverflowError(
                f"当天序号已达 {next_seq}，超出 9999 上限",
                details={
                    "tenant_id": str(tenant_id),
                    "date_key": str(date_key),
                },
            )
        return next_seq

    # ----------------------- update_state (FB7 复用 U04) ----------------------- #

    async def update_state(
        self,
        *,
        settlement_id: UUID,
        tenant_id: UUID,
        from_state_value: str,
        to_state_value: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> Settlement | None:
        """乐观并发 UPDATE WHERE old_state RETURNING（FB7 模式）.

        WHERE 条件包含：
        - ``id = :settlement_id``
        - ``tenant_id = :tenant_id``（多租户防护，与 RLS 双保险）
        - ``settlement_status = :from_state_value``（旧状态防护）

        **不含 ``is_active`` 字段**（FB3：财务记录无软删除字段）。

        Returns:
            ``Settlement`` 实例（推进成功）；
            ``None`` 表示并发冲突 / 已被推进 / 跨租户 — service 层应抛
            ``StateTransitionConflictError``。

        Args:
            extra_fields: 状态推进时一并写入的字段（如 reviewed_by / payment_date / paid_by 等）。
        """
        values: dict[str, Any] = dict(extra_fields or {})
        values["settlement_status"] = to_state_value
        values["updated_at"] = func.now()

        stmt = (
            update(Settlement)
            .where(
                Settlement.id == settlement_id,
                Settlement.tenant_id == tenant_id,
                Settlement.settlement_status == from_state_value,
            )
            .values(**values)
            .returning(Settlement)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None
        settlement: Settlement = row[0]
        # RETURNING 可能命中 session 身份映射中的旧实例（status 等字段未同步）；
        # 刷新以反映 DB 最新状态，避免调用方读到旧值（FB7）。
        await self._session.refresh(settlement)
        return settlement

    # ----------------------- update_total_amount ----------------------- #

    async def update_total_amount(
        self,
        *,
        settlement_id: UUID,
        tenant_id: UUID,
        total_amount: Decimal,
    ) -> Settlement | None:
        """单字段更新 total_amount（add_extra_item 时 service 层调用）.

        WHERE 含 tenant_id + settlement_status='待付款'（仅"待付款"允许 extra_item，BR-U05-40）。
        """
        stmt = (
            update(Settlement)
            .where(
                Settlement.id == settlement_id,
                Settlement.tenant_id == tenant_id,
                Settlement.settlement_status == "待付款",
            )
            .values(total_amount=total_amount, updated_at=func.now())
            .returning(Settlement)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None
        settlement: Settlement = row[0]
        await self._session.refresh(settlement)
        return settlement


    # ----------------------- extra_item ----------------------- #

    async def list_extra_items(
        self, *, settlement_id: UUID
    ) -> Sequence[SettlementExtraItem]:
        stmt = (
            select(SettlementExtraItem)
            .where(SettlementExtraItem.settlement_id == settlement_id)
            .order_by(SettlementExtraItem.created_at.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def sum_extra_items(self, *, settlement_id: UUID) -> Decimal:
        stmt = select(
            func.coalesce(
                func.sum(SettlementExtraItem.amount), Decimal("0")
            )
        ).where(SettlementExtraItem.settlement_id == settlement_id)
        result = await self._session.execute(stmt)
        return Decimal(result.scalar_one() or 0)

    def add_extra_item(self, item: SettlementExtraItem) -> None:
        self._session.add(item)

    # ----------------------- list_with_filters ----------------------- #

    async def list_with_filters(
        self,
        *,
        tenant_id: UUID,
        filters: SettlementListFilters,
        page: int,
        page_size: int,
        current_user_id: UUID | None = None,
    ) -> tuple[Sequence[Settlement], int]:
        """列表查询。

        关键约束：
        - PR 角色（filters.is_my_only=True）→ 自动加 WHERE pr_id=current_user_id
        - 多筛选 + 分页 + total 统计
        - 不需要 CTE（settlement 字段全部持久化无衍生字段，与 U04 不同）
        """
        stmt = select(Settlement).where(Settlement.tenant_id == tenant_id)

        if filters.is_my_only and current_user_id is not None:
            stmt = stmt.where(Settlement.pr_id == current_user_id)

        if filters.settlement_status:
            stmt = stmt.where(
                Settlement.settlement_status == filters.settlement_status
            )

        if filters.promotion_id:
            stmt = stmt.where(Settlement.promotion_id == filters.promotion_id)
        if filters.blogger_id:
            stmt = stmt.where(Settlement.blogger_id == filters.blogger_id)
        if filters.style_id:
            stmt = stmt.where(Settlement.style_id == filters.style_id)
        if filters.pr_id:
            stmt = stmt.where(Settlement.pr_id == filters.pr_id)
        if filters.reviewed_by:
            stmt = stmt.where(Settlement.reviewed_by == filters.reviewed_by)
        if filters.paid_by:
            stmt = stmt.where(Settlement.paid_by == filters.paid_by)

        if filters.created_at_from:
            stmt = stmt.where(Settlement.created_at >= filters.created_at_from)
        if filters.created_at_to:
            stmt = stmt.where(
                Settlement.created_at < filters.created_at_to + timedelta(days=1)
            )
        if filters.payment_date_from:
            stmt = stmt.where(
                Settlement.payment_date >= filters.payment_date_from
            )
        if filters.payment_date_to:
            stmt = stmt.where(
                Settlement.payment_date <= filters.payment_date_to
            )

        if filters.amount_from is not None:
            stmt = stmt.where(Settlement.total_amount >= filters.amount_from)
        if filters.amount_to is not None:
            stmt = stmt.where(Settlement.total_amount <= filters.amount_to)
        if filters.payment_amount_from is not None:
            stmt = stmt.where(
                Settlement.payment_amount >= filters.payment_amount_from
            )
        if filters.payment_amount_to is not None:
            stmt = stmt.where(
                Settlement.payment_amount <= filters.payment_amount_to
            )

        if filters.keyword:
            # 命中 idx_settlement_no_trgm GIN 索引
            pattern = f"%{filters.keyword}%"
            stmt = stmt.where(Settlement.settlement_no.ilike(pattern))

        # total 统计
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())

        stmt = (
            stmt.order_by(Settlement.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return items, total


    # ----------------------- daily_summary 双口径 (FB7) ----------------------- #

    async def daily_summary_as_of(
        self,
        *,
        tenant_id: UUID,
        date_value: date,
    ) -> dict[str, dict[str, Any]]:
        """口径 B：截至当日各状态快照（FB7）.

        含义：截至 :date_value（Asia/Shanghai 当天末），各状态的 settlement 计数与
        金额聚合。``created_at`` 为 UTC TIMESTAMPTZ，比较前转换到 Asia/Shanghai
        本地日期（FB8 时区一致），避免 UTC 与本地日期错位导致漏算/多算。
        SQL: GROUP BY settlement_status，走 ``idx_settlement_tenant_status``。
        """
        stmt = text(
            """
            SELECT
                settlement_status,
                COUNT(*) AS cnt,
                COALESCE(SUM(total_amount), 0) AS sum_amt
            FROM settlement
            WHERE tenant_id = :tid
              AND (created_at AT TIME ZONE 'Asia/Shanghai')::date <= :date_value
            GROUP BY settlement_status
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "tid": tenant_id,
                "date_value": date_value,
            },
        )
        return {
            row.settlement_status: {
                "count": int(row.cnt),
                "total_amount": str(row.sum_amt),
            }
            for row in result.mappings().all()
        }

    async def daily_summary_activity(
        self,
        *,
        tenant_id: UUID,
        date_value: date,
    ) -> dict[str, dict[str, Any]]:
        """口径 A：当天发生的动作（FB7 含 audit_log JOIN）.

        4 类动作：
        - newly_created：当天 created_at 的 settlement
        - newly_approved：当天 audit "settlement.review.approve"
        - newly_paid：当天 payment_date 的 settlement
        - newly_rejected：当天 audit "settlement.review.reject"
        """
        stmt = text(
            """
            WITH
              newly_created AS (
                SELECT
                  COUNT(*) AS cnt,
                  COALESCE(SUM(total_amount), 0) AS sum_amt
                FROM settlement
                WHERE tenant_id = :tid
                  AND (created_at AT TIME ZONE 'Asia/Shanghai')::date = :date_value
              ),
              newly_paid AS (
                SELECT
                  COUNT(*) AS cnt,
                  COALESCE(SUM(total_amount), 0) AS sum_amt
                FROM settlement
                WHERE tenant_id = :tid
                  AND payment_date = :date_value
              ),
              newly_approved AS (
                SELECT
                  COUNT(DISTINCT al.resource_id) AS cnt,
                  COALESCE(SUM(s.total_amount), 0) AS sum_amt
                FROM audit_log al
                JOIN settlement s ON s.id::text = al.resource_id
                WHERE al.tenant_id = :tid
                  AND al.action = 'settlement.review.approve'
                  AND (al.created_at AT TIME ZONE 'Asia/Shanghai')::date = :date_value
              ),
              newly_rejected AS (
                SELECT
                  COUNT(DISTINCT al.resource_id) AS cnt,
                  COALESCE(SUM(s.total_amount), 0) AS sum_amt
                FROM audit_log al
                JOIN settlement s ON s.id::text = al.resource_id
                WHERE al.tenant_id = :tid
                  AND al.action = 'settlement.review.reject'
                  AND (al.created_at AT TIME ZONE 'Asia/Shanghai')::date = :date_value
              )
            SELECT
              (SELECT cnt FROM newly_created) AS created_cnt,
              (SELECT sum_amt FROM newly_created) AS created_amt,
              (SELECT cnt FROM newly_approved) AS approved_cnt,
              (SELECT sum_amt FROM newly_approved) AS approved_amt,
              (SELECT cnt FROM newly_paid) AS paid_cnt,
              (SELECT sum_amt FROM newly_paid) AS paid_amt,
              (SELECT cnt FROM newly_rejected) AS rejected_cnt,
              (SELECT sum_amt FROM newly_rejected) AS rejected_amt
            """
        )
        params = {
            "tid": tenant_id,
            "date_value": date_value,
        }
        row = (await self._session.execute(stmt, params)).one()
        return {
            "newly_created": {
                "count": int(row.created_cnt or 0),
                "total_amount": str(row.created_amt or 0),
            },
            "newly_approved": {
                "count": int(row.approved_cnt or 0),
                "total_amount": str(row.approved_amt or 0),
            },
            "newly_paid": {
                "count": int(row.paid_cnt or 0),
                "total_amount": str(row.paid_amt or 0),
            },
            "newly_rejected": {
                "count": int(row.rejected_cnt or 0),
                "total_amount": str(row.rejected_amt or 0),
            },
        }


__all__ = [
    "SettlementListFilters",
    "SettlementRepository",
]
