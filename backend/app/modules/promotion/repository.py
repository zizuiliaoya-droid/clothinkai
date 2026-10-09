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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, delete, exists, func, select, text, union_all, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import promotion_sequence_lock_duration_seconds
from app.modules.negotiation.models import Negotiation
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.models import Sku, Style
from app.modules.promotion.display_name import (
    PROMOTION_DISPLAY_SHORT_NAME_SQL,
    display_short_name_sql,
)
from app.modules.promotion.exceptions import SequenceOverflowError
from app.modules.promotion.models import BloggerRetrospective, Promotion, PromotionItem
from app.modules.promotion.stage_calculator import stage_sql_expr
from app.modules.promotion.urge_calculator import URGE_STATUS_SQL_EXPR

# list_with_cte 把 raw row 重组成 ORM 实例时要往构造器里喂哪些列。
#
# **从 ORM 列定义反推，不要手写白名单。** 原来这里是一份手写的列名元组，漏了
# cooperation_mode / return_shipping_fee / return_waybill / review_reason_category /
# in_store_order 五列 —— 后果是这些列在列表接口里恒为 null，详情接口正常，
# 前端表格一片「—」，既不报错也不告警，上线很久才被发现。
#
# 生成列必须排除：SQLAlchemy 不允许给 Computed 列赋值。
_RECONSTRUCT_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in Promotion.__table__.columns if c.computed is None
)

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
    # 仓库打单用：按 source_extra 里的「打单地址」/「发货单号」是否已填筛选。
    # 这两个筛选必须在服务端做 —— 打单单量只占推广总量的极小比例，
    # 客户端过滤会既慢（要拉全量）又漏（只看得到当前页）。
    has_print_address: bool | None = None
    has_waybill: bool | None = None


@dataclass(frozen=True)
class PromotionListRow:
    """list_with_cte 返回的轻量行（含计算字段、款式主图 key 与商品归属）。"""

    promotion: Promotion
    urge_status: str
    dual_platform: bool
    style_main_image_key: str | None = None
    goods_code: str | None = None
    goods_is_suit: bool = False
    # 7a-8：品名（商品简称，没填回落快照）与归属商品的名字，规则见 display_name.py
    display_short_name: str | None = None
    goods_title: str | None = None
    goods_short_name: str | None = None
    # 流程线 3.8 当前阶段（stage_calculator.stage_sql_expr）；PR-10 之前不进响应
    stage: str | None = None


@dataclass(frozen=True)
class PromotionAttachmentRefs:
    payment_qr_attachment_id: UUID | None = None
    payment_qr_r2_key: str | None = None
    payment_qr_status: str | None = None
    settlement_proof_r2_key: str | None = None
    settlement_proof_status: str | None = None


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PromotionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / count ----------------------- #

    async def count_by_sku(self, sku_id: UUID) -> int:
        """统计引用这个 SKU 的推广单数；租户隔离由 RLS 保证。

        ``promotion.sku_id`` 与商品明细 ``promotion_item.sku_id`` 两处都算，按推广单去重
        （同一张单两处都引用只算 1，流程线 7.8）。
        """
        refs = union_all(
            select(Promotion.id.label("promotion_id")).where(Promotion.sku_id == sku_id),
            select(PromotionItem.promotion_id.label("promotion_id")).where(
                PromotionItem.sku_id == sku_id
            ),
        ).subquery()
        stmt = select(func.count(func.distinct(refs.c.promotion_id)))
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_by_blogger(self, blogger_id: UUID) -> int:
        """统计博主的全部历史推广引用；租户隔离由 RLS 保证。"""
        stmt = select(func.count()).select_from(Promotion).where(Promotion.blogger_id == blogger_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def get_by_id(
        self, promotion_id: UUID, *, include_inactive: bool = False
    ) -> Promotion | None:
        promotion = await self._session.get(Promotion, promotion_id)
        if promotion is None:
            return None
        if not promotion.is_active and not include_inactive:
            return None
        return promotion

    async def get_payment_attachment_refs(
        self, *, tenant_id: UUID, promotion_ids: list[UUID]
    ) -> dict[UUID, PromotionAttachmentRefs]:
        """一次批量取得收款码与结算付款凭证引用，避免列表 N+1。"""
        if not promotion_ids:
            return {}
        sql = text(
            """
            SELECT p.id AS promotion_id, p.payment_qr_attachment_id,
                   qr.r2_key AS payment_qr_r2_key, qr.status AS payment_qr_status,
                   proof.r2_key AS settlement_proof_r2_key,
                   proof.status AS settlement_proof_status
            FROM promotion p
            LEFT JOIN attachment qr
              ON qr.id=p.payment_qr_attachment_id AND qr.tenant_id=p.tenant_id
            LEFT JOIN settlement s
              ON s.promotion_id=p.id AND s.tenant_id=p.tenant_id
            LEFT JOIN attachment proof
              ON proof.id=s.payment_proof_attachment_id AND proof.tenant_id=s.tenant_id
            WHERE p.tenant_id=:tenant_id
              AND p.id = ANY(:promotion_ids)
            """
        ).bindparams(
            bindparam("tenant_id", type_=PGUUID(as_uuid=True)),
            bindparam(
                "promotion_ids",
                type_=ARRAY(PGUUID(as_uuid=True)),
            ),
        )
        result = await self._session.execute(
            sql,
            {"tenant_id": tenant_id, "promotion_ids": promotion_ids},
        )
        return {
            row["promotion_id"]: PromotionAttachmentRefs(
                payment_qr_attachment_id=row["payment_qr_attachment_id"],
                payment_qr_r2_key=row["payment_qr_r2_key"],
                payment_qr_status=row["payment_qr_status"],
                settlement_proof_r2_key=row["settlement_proof_r2_key"],
                settlement_proof_status=row["settlement_proof_status"],
            )
            for row in result.mappings().all()
        }

    async def negotiator_ids(self, promotion_ids: Sequence[UUID]) -> dict[UUID, UUID]:
        """推广单 → 谈款的 PR（矩阵快照的 ``negotiator_id``）。没有谈款的单不在结果里；
        一张推广单正常只有一张谈款，万一多张取最新那张。"""
        if not promotion_ids:
            return {}
        stmt = (
            select(Negotiation.promotion_id, Negotiation.pr_id)
            .where(Negotiation.promotion_id.in_(list(promotion_ids)))
            .distinct(Negotiation.promotion_id)
            .order_by(Negotiation.promotion_id, Negotiation.created_at.desc(), Negotiation.id)
        )
        return {row[0]: row[1] for row in (await self._session.execute(stmt)).all() if row[0]}

    async def get_by_internal_code(self, internal_code: str) -> Promotion | None:
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
        display_short_name / blogger_nickname）。RLS 自动按 tenant 过滤。

        ``display_short_name`` 是企微模板 ``{商品简称}`` 用的品名（商品简称，没填回落
        快照，规则见 ``display_name.py``）；快照列照旧返回。
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
                {display_short_name_sql(promotion="promotion")} AS display_short_name,
                blogger.nickname AS blogger_nickname
            FROM promotion
            JOIN blogger ON blogger.id = promotion.blogger_id
            LEFT JOIN goods_main g
              ON g.id = promotion.goods_main_id AND g.tenant_id = promotion.tenant_id
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

    # ----------------------- 商品归属 ----------------------- #

    async def resolve_owner_goods_id(self, style_id: UUID) -> UUID | None:
        """推定主商品：非套装优先、货号次之。没归到任何商品时返回 None。

        与报表兜底 SQL 同序，保证不传 goods_main_id 时行为与旧口径一致。
        """
        sql = text(
            """
            SELECT g.id
            FROM goods_style_item gi
            JOIN goods_main g ON g.id = gi.goods_main_id
            WHERE gi.style_id = :style_id AND gi.is_active = true
              AND g.is_deleted = false
            ORDER BY g.is_suit, g.goods_code
            LIMIT 1
            """
        )
        result: UUID | None = (
            await self._session.execute(sql, {"style_id": style_id})
        ).scalar_one_or_none()
        return result

    async def goods_contains_style(self, *, goods_main_id: UUID, style_id: UUID) -> bool:
        """商品是否包含该款式 —— 防止把推广挂到不相干的商品上。"""
        stmt = select(
            exists().where(
                GoodsStyleItem.goods_main_id == goods_main_id,
                GoodsStyleItem.style_id == style_id,
                GoodsStyleItem.is_active.is_(True),
            )
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    async def sum_goods_sample_cost(self, goods_main_id: UUID) -> Decimal | None:
        """商品的样品成本 = 启用成员款式的单件货品成本之和。

        PRD：送拍 / 置换按 ``tb_item_id`` 查子表全部 ``is_enable=1`` 记录求和。
        套装就是这么算出总样品成本的 —— 寄给博主的是整套衣服。

        全部成员都没填成本时返回 None 而不是 0，让调用方能区分「这套不要钱」和
        「成本还没录」。生产上那个套装的两个成员都没有 SKU 成本价，就是后一种。
        """
        sql = text(
            """
            SELECT SUM(gi.single_goods_cost) AS total
            FROM goods_style_item gi
            WHERE gi.goods_main_id = :goods_main_id
              AND gi.is_active = true
              AND gi.single_goods_cost IS NOT NULL
            """
        )
        total: Decimal | None = (
            await self._session.execute(sql, {"goods_main_id": goods_main_id})
        ).scalar_one_or_none()
        return total

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
            result = await self._session.execute(stmt, {"tid": tenant_id, "dk": date_key})
            next_seq = int(result.scalar_one())
        finally:
            promotion_sequence_lock_duration_seconds.observe(time.perf_counter() - start)

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
            "retro_status",  # PRD V1.4 改动 4，第 4 个并行状态机
        }:
            raise ValueError(f"unsupported state field: {from_state_field}")

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

    # ----------------------- 金额时间线 ----------------------- #

    async def amount_log(
        self, *, tenant_id: UUID, promotion_id: UUID, limit: int = 100
    ) -> list[Mapping[str, Any]]:
        """某单据的金额变更时间线，倒序。"""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT l.id, l.field_name, l.before_value, l.after_value,
                           l.change_source, l.changed_by, l.created_at,
                           COALESCE(u.display_name, u.username) AS changed_by_name
                    FROM promotion_amount_log l
                    LEFT JOIN "user" u ON u.id = l.changed_by
                    WHERE l.tenant_id = :tenant_id AND l.promotion_id = :promotion_id
                    ORDER BY l.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "promotion_id": promotion_id, "limit": limit},
            )
        ).mappings()
        return [dict(r) for r in rows]

    # ----------------------- 复盘（PRD V1.4 改动 4） ----------------------- #

    def add_retrospective(self, retro: BloggerRetrospective) -> None:
        self._session.add(retro)

    async def latest_retrospective(
        self, *, tenant_id: UUID, promotion_id: UUID
    ) -> BloggerRetrospective | None:
        """本单最新的一条复盘。

        「当前生效」就是最新那条 —— 被主管打回重写时追加新行，旧的留着做留痕。
        """
        stmt = (
            select(BloggerRetrospective)
            .where(
                BloggerRetrospective.tenant_id == tenant_id,
                BloggerRetrospective.promotion_id == promotion_id,
            )
            .order_by(BloggerRetrospective.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def blogger_retrospectives(
        self, *, tenant_id: UUID, blogger_id: UUID, limit: int = 20
    ) -> list[Mapping[str, Any]]:
        """某博主的历史复盘，按时间倒序（PRD：hover 卡展示全部历史复盘）。

        只取已确认的 —— 没过主管的复盘是草稿，进了博主档案会误导下次选博主的人。
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT r.id, r.blogger_id, r.promotion_id, r.content,
                           r.created_by, r.confirmed_by, r.confirmed_at, r.created_at,
                           p.internal_code AS promotion_internal_code,
                           p.style_code_snapshot AS style_code,
                           COALESCE(cu.display_name, cu.username) AS created_by_name,
                           COALESCE(fu.display_name, fu.username) AS confirmed_by_name
                    FROM blogger_retrospective r
                    JOIN promotion p ON p.id = r.promotion_id
                    LEFT JOIN "user" cu ON cu.id = r.created_by
                    LEFT JOIN "user" fu ON fu.id = r.confirmed_by
                    WHERE r.tenant_id = :tenant_id
                      AND r.blogger_id = :blogger_id
                      AND r.confirmed_at IS NOT NULL
                    ORDER BY r.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "blogger_id": blogger_id, "limit": limit},
            )
        ).mappings()
        return [dict(r) for r in rows]

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
        """列表查询，CTE 注入 ``urge_status`` / ``dual_platform`` / ``stage`` 计算列。

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
                   s.main_image_key AS style_main_image_key,
                   g.goods_code AS goods_code,
                   COALESCE(g.is_suit, false) AS goods_is_suit,
                   NULLIF(BTRIM(g.short_name), '') AS goods_short_name,
                   g.goods_title AS goods_title,
                   {PROMOTION_DISPLAY_SHORT_NAME_SQL} AS display_short_name,
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
            LEFT JOIN style s
              ON s.id = p.style_id AND s.tenant_id = p.tenant_id
            LEFT JOIN goods_main g
              ON g.id = p.goods_main_id AND g.tenant_id = p.tenant_id
            WHERE p.tenant_id = :tenant_id
        ),
        -- 阶段放在只有推广单列的这一层算：style 也有 is_active，放进上面的 JOIN 会歧义
        staged AS (
            SELECT base.*, {stage_sql_expr()} AS stage
            FROM base
        )
        SELECT * FROM staged WHERE 1=1
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
            clauses.append("(like_count IS NULL OR like_count < :hit_th)")
            params["hit_th"] = filters.hit_threshold
        if filters.keyword:
            # 前三列命中 GIN trgm 索引（idx_promotion_internal_code_trgm 等）。
            # 商品简称 / 全称 / 编码来自 LEFT JOIN 的 goods_main（7a-8）：界面上不再显示
            # 商品编码，但仍要能按编码搜到（业务方 10-06「隐藏显示 ≠ 不可查」）。不加索引。
            clauses.append(
                "(internal_code ILIKE :kw "
                "OR style_code_snapshot ILIKE :kw "
                "OR style_short_name_snapshot ILIKE :kw "
                "OR goods_short_name ILIKE :kw "
                "OR goods_title ILIKE :kw "
                "OR goods_code ILIKE :kw)"
            )
            params["kw"] = f"%{filters.keyword}%"
        # 表达式与 idx_promotion_print_address 部分索引的谓词保持一致，否则不命中索引。
        if filters.has_print_address is not None:
            op = "<>" if filters.has_print_address else "="
            clauses.append(f"COALESCE(BTRIM(source_extra->>'打单地址'), '') {op} ''")
        if filters.has_waybill is not None:
            op = "<>" if filters.has_waybill else "="
            clauses.append(f"COALESCE(BTRIM(source_extra->>'发货单号'), '') {op} ''")

        where_extra = ""
        if clauses:
            where_extra = " AND " + " AND ".join(clauses)

        # ------ 1. count ------
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}{where_extra}) AS c"
        total = int((await self._session.execute(text(count_sql), params)).scalar_one())

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
            promotion = Promotion(**{col: row[col] for col in _RECONSTRUCT_COLUMNS if col in row})
            # 防止重组的 ORM 实例污染 session unit of work
            if promotion in self._session:
                self._session.expunge(promotion)
            rows.append(
                PromotionListRow(
                    promotion=promotion,
                    urge_status=row["urge_status"],
                    dual_platform=bool(row["dual_platform_calc"]),
                    style_main_image_key=row["style_main_image_key"],
                    goods_code=row["goods_code"],
                    goods_is_suit=bool(row["goods_is_suit"]),
                    display_short_name=row["display_short_name"],
                    goods_title=row["goods_title"],
                    goods_short_name=row["goods_short_name"],
                    stage=row["stage"],
                )
            )
        return rows, total


# ---------------------------------------------------------------------------
# 商品明细（promotion_item，流程线 M1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionItemView:
    """明细一行 + 款式 / SKU 上要显示的列（颜色尺码实时读 SKU）。"""

    promotion_id: UUID
    style_id: UUID
    sku_id: UUID
    color: str
    size: str
    style_name: str
    style_short_name: str | None
    style_main_image_key: str | None


class PromotionItemRepository:
    """推广单商品明细的读写。成员与 SKU 归属的校验在 service（不写业务规则）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_promotions(
        self, promotion_ids: Sequence[UUID]
    ) -> dict[UUID, list[PromotionItemView]]:
        """一次查出这些推广单的全部明细（列表一页一条查询），按 ``sort_order`` 排。"""
        if not promotion_ids:
            return {}
        stmt = (
            select(
                PromotionItem.promotion_id,
                PromotionItem.style_id,
                PromotionItem.sku_id,
                Sku.color,
                Sku.size,
                Style.style_name,
                Style.short_name,
                Style.main_image_key,
            )
            .join(Sku, Sku.id == PromotionItem.sku_id)
            .join(Style, Style.id == PromotionItem.style_id)
            .where(PromotionItem.promotion_id.in_(list(promotion_ids)))
            .order_by(PromotionItem.promotion_id, PromotionItem.sort_order, PromotionItem.id)
        )
        grouped: dict[UUID, list[PromotionItemView]] = {}
        for row in (await self._session.execute(stmt)).all():
            grouped.setdefault(row[0], []).append(PromotionItemView(*row))
        return grouped

    async def replace(
        self,
        *,
        tenant_id: UUID,
        promotion_id: UUID,
        rows: Sequence[tuple[UUID, UUID]],
    ) -> None:
        """整组替换：删掉这张单的旧明细，按 ``rows`` 的顺序插入（``sort_order`` 从 0）。不提交。"""
        await self._session.execute(
            delete(PromotionItem).where(PromotionItem.promotion_id == promotion_id)
        )
        for order, (style_id, sku_id) in enumerate(rows):
            self._session.add(
                PromotionItem(
                    tenant_id=tenant_id,
                    promotion_id=promotion_id,
                    style_id=style_id,
                    sku_id=sku_id,
                    sort_order=order,
                )
            )
        await self._session.flush()

    async def members_of(self, *, goods_main_id: UUID | None, style_id: UUID) -> list[UUID]:
        """明细应有的款式集合：套装 = ``goods_style_item`` 里启用的成员（按 ``sort_order``）；
        没有归属商品、单品（或套装没有启用成员）= ``[style_id]``。"""
        if goods_main_id is None:
            return [style_id]
        stmt = (
            select(GoodsStyleItem.style_id)
            .join(GoodsMain, GoodsMain.id == GoodsStyleItem.goods_main_id)
            .where(
                GoodsStyleItem.goods_main_id == goods_main_id,
                GoodsStyleItem.is_active.is_(True),
                GoodsMain.is_suit.is_(True),
            )
            .order_by(GoodsStyleItem.sort_order, GoodsStyleItem.style_id)
        )
        members = list((await self._session.execute(stmt)).scalars().all())
        return members or [style_id]

    async def members_by_goods(self, goods_main_ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        """``members_of`` 的整页批量版：只回有启用成员的套装；不在结果里的按 ``[style_id]``。"""
        if not goods_main_ids:
            return {}
        stmt = (
            select(GoodsStyleItem.goods_main_id, GoodsStyleItem.style_id)
            .join(GoodsMain, GoodsMain.id == GoodsStyleItem.goods_main_id)
            .where(
                GoodsStyleItem.goods_main_id.in_(list(goods_main_ids)),
                GoodsStyleItem.is_active.is_(True),
                GoodsMain.is_suit.is_(True),
            )
            .order_by(
                GoodsStyleItem.goods_main_id, GoodsStyleItem.sort_order, GoodsStyleItem.style_id
            )
        )
        grouped: dict[UUID, list[UUID]] = {}
        for goods_id, style_id in (await self._session.execute(stmt)).all():
            grouped.setdefault(goods_id, []).append(style_id)
        return grouped

    async def set_style_sku(
        self, *, tenant_id: UUID, promotion_id: UUID, style_id: UUID, sku_id: UUID | None
    ) -> None:
        """改一个款式那一行的 SKU（PATCH ``sku_id`` 同步主款式那行）：有就改、没有就追加到最后；
        ``sku_id`` 为 None 删掉那一行（明细的 sku 不可空）。不提交。"""
        if sku_id is None:
            await self._session.execute(
                delete(PromotionItem).where(
                    PromotionItem.promotion_id == promotion_id, PromotionItem.style_id == style_id
                )
            )
            return
        updated = await self._session.execute(
            update(PromotionItem)
            .where(PromotionItem.promotion_id == promotion_id, PromotionItem.style_id == style_id)
            .values(sku_id=sku_id)
            .returning(PromotionItem.id)
        )
        if updated.first() is not None:
            return
        next_order = (
            await self._session.execute(
                select(func.coalesce(func.max(PromotionItem.sort_order) + 1, 0)).where(
                    PromotionItem.promotion_id == promotion_id
                )
            )
        ).scalar_one()
        self._session.add(
            PromotionItem(
                tenant_id=tenant_id,
                promotion_id=promotion_id,
                style_id=style_id,
                sku_id=sku_id,
                sort_order=next_order,
            )
        )
        await self._session.flush()

    async def skus_by_ids(self, sku_ids: Sequence[UUID]) -> dict[UUID, Sku]:
        """按 id 批量取 SKU（含已删的，由调用方判 ``is_deleted``）。"""
        if not sku_ids:
            return {}
        stmt = select(Sku).where(Sku.id.in_(list(sku_ids)))
        return {sku.id: sku for sku in (await self._session.execute(stmt)).scalars().all()}


__all__ = [
    "PromotionAttachmentRefs",
    "PromotionItemRepository",
    "PromotionItemView",
    "PromotionListFilters",
    "PromotionListRow",
    "PromotionRepository",
]
