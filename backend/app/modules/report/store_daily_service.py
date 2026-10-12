"""U14 StoreDailyService（店铺数据看板聚合 + 手动字段 upsert）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import report_query_duration_seconds
from app.modules.auth.models import User
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import StoreDailyRepository
from app.modules.report.advanced_schemas import (
    StoreDailyManualUpdate,
    StoreDailyRow,
    StoreReport,
)
from app.modules.report.domain import bucket_start
from app.modules.report.exceptions import StoreDailyDateTooEarlyError, StoreDailyFutureDateError
from app.modules.report.extra_metrics import aggregate_extra
from app.modules.report.store_buckets import bucket_store_rows
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.report.work_progress_models import StoreDaily

# 新三页的 record_source / 耗时标签（旧接口仍用 store_daily）
_REPORT_LABELS = {"day": "store_daily", "week": "store_weekly", "month": "store_monthly"}

# 手填层的 5 个字段（审计改前改后、接口响应都按这份）
_MANUAL_FIELDS = (
    "ad_spend_total",
    "zhitongche_spend",
    "yinli_spend",
    "refund_only_amount",
    "remark",
)
# 手填日期下限（设计 5.1）
_EARLIEST_MANUAL_DAY = date(2020, 1, 1)


def _manual_snapshot(values: Mapping[Any, Any]) -> dict[str, str | None]:
    """审计用：5 个手填字段转成 JSON 可存的值（金额 → 字符串，未填 → None）。"""
    return {f: None if values[f] is None else str(values[f]) for f in _MANUAL_FIELDS}


def _sum_optional(current: Decimal | None, value: Decimal | None) -> Decimal | None:
    """手填的广告消耗：整个桶都没填保持 None（页面显示「—」），填了几天就加那几天。"""
    if value is None:
        return current
    return value if current is None else current + value


class StoreDailyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StoreDailyRepository(session)
        self._summary = SummaryReadRepository(session)
        self._audit = AuditService(session)

    async def get_dashboard(
        self, tenant_id: UUID, time_range: tuple[date, date], granularity: str = "day"
    ) -> list[StoreDailyRow]:
        """店铺数据。区间被汇总表完整覆盖就读汇总表，否则实时。

        手填的 3 个广告消耗两条路径都是读取时 LEFT JOIN ``store_daily``，填完立即生效；
        extra（千牛导出的其余几十列）汇总表不存，始终实时。

        ``granularity`` 默认 day（一天一行）；week / month / year 时把日行按桶合并，
        date 是桶首日、按桶升序。页面与导出都从这里取，周 / 月 / 年怎么合并只有这一份。
        """
        date_from, date_to = time_range
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        with report_query_duration_seconds.labels("store_daily").time():
            if fresh.source == "summary":
                record_source("store_daily", "summary")
                rows = await self._summary.store_daily(
                    tenant_id=tenant_id, date_from=date_from, date_to=date_to
                )
            else:
                record_source("store_daily", "live")
                rows = await self._repo.aggregate(
                    tenant_id=tenant_id, date_from=date_from, date_to=date_to
                )
        extra_by_bucket = await self._aggregate_extra(tenant_id, date_from, date_to, granularity)
        result = [self._to_row(r) for r in rows]
        if granularity != "day":
            result = self._merge_buckets(result, granularity)
        for row in result:
            row.extra = extra_by_bucket.get(str(row.date), {})
        return result

    async def get_store_report(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        granularity: Literal["day", "week", "month"],
    ) -> StoreReport:
        """店铺日明细 / 周汇总 / 月度汇总（7e R4，设计 2.4）。

        整个请求区间只判一次来源（一个响应只有一种来源）：汇总读 ``shop_daily_summary``、
        实时读 ``StoreDailyRepository.aggregate``，两路都是日行且已 LEFT JOIN ``store_daily``；
        周 / 月由 ``bucket_store_rows`` 在日行上逐天套默 13 与仅退款后分桶，**不读**周 / 月汇总表。
        """
        label = _REPORT_LABELS[granularity]
        date_from, date_to = time_range
        rows = await self._read_day_rows(tenant_id, date_from, date_to, label)
        items = bucket_store_rows(rows, granularity, date_from, date_to)
        extra_by_bucket = await self._aggregate_extra(tenant_id, date_from, date_to, granularity)
        for item in items:
            item.extra = extra_by_bucket.get(str(bucket_start(item.date_from, granularity)), {})
        return StoreReport(
            granularity=granularity,
            date_from=date_from,
            date_to=date_to,
            items=items,
            recorded_days=sum(i.refund_only_days for i in items),
            total_days=sum(i.day_count for i in items),
        )

    async def _read_day_rows(
        self, tenant_id: UUID, date_from: date, date_to: date, label: str
    ) -> list[Mapping[str, Any]]:
        """``[date_from, date_to]`` 的店铺日行：区间被汇总表完整覆盖就读汇总，否则实时。"""
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        with report_query_duration_seconds.labels(label).time():
            if fresh.source == "summary":
                record_source(label, "summary")
                return await self._summary.store_daily(
                    tenant_id=tenant_id, date_from=date_from, date_to=date_to
                )
            record_source(label, "live")
            return await self._repo.aggregate(
                tenant_id=tenant_id, date_from=date_from, date_to=date_to
            )

    async def _aggregate_extra(
        self, tenant_id: UUID, date_from: date, date_to: date, granularity: str
    ) -> dict[str, dict[str, str | None]]:
        """按日（或周 / 月 / 年桶）聚合 qianniu_daily.extra（对齐 final.xlsx 店铺数据 24 列）。

        规则在 ``extra_metrics``：比率 / 均值 / 评分不相加，常用比率按分子分母重算；
        累计列只有按日（同一天跨商品）时相加，按周 / 月 / 年跨了天就不加。
        """
        sql = text(
            "SELECT date, extra FROM qianniu_daily "
            "WHERE tenant_id = :t AND date BETWEEN :f AND :to AND extra IS NOT NULL"
        )
        rows = (
            await self._session.execute(sql, {"t": str(tenant_id), "f": date_from, "to": date_to})
        ).all()
        by_bucket: dict[str, list[Any]] = defaultdict(list)
        for d, extra in rows:
            by_bucket[str(bucket_start(d, granularity))].append(extra)
        same_day = granularity == "day"
        return {
            bucket: aggregate_extra(extras, same_day=same_day)
            for bucket, extras in by_bucket.items()
        }

    @staticmethod
    def _merge_buckets(rows: list[StoreDailyRow], granularity: str) -> list[StoreDailyRow]:
        """日行 → 桶行：访客、支付额、支付订单相加；广告消耗按 ``_sum_optional``。"""
        buckets: dict[date, StoreDailyRow] = {}
        for row in rows:
            start = bucket_start(row.date, granularity)
            merged = buckets.get(start)
            if merged is None:
                buckets[start] = StoreDailyRow(
                    date=start,
                    visitors=row.visitors,
                    pay_amount=row.pay_amount,
                    pay_orders=row.pay_orders,
                    ad_spend_total=row.ad_spend_total,
                    zhitongche_spend=row.zhitongche_spend,
                    yinli_spend=row.yinli_spend,
                )
                continue
            merged.visitors += row.visitors
            merged.pay_amount += row.pay_amount
            merged.pay_orders += row.pay_orders
            merged.ad_spend_total = _sum_optional(merged.ad_spend_total, row.ad_spend_total)
            merged.zhitongche_spend = _sum_optional(merged.zhitongche_spend, row.zhitongche_spend)
            merged.yinli_spend = _sum_optional(merged.yinli_spend, row.yinli_spend)
        return [buckets[start] for start in sorted(buckets)]

    @staticmethod
    def _to_row(r: Mapping[str, Any]) -> StoreDailyRow:
        return StoreDailyRow(
            date=r["date"],
            visitors=int(r["visitors"] or 0),
            # 两条路径数值相等但写法可能不同（"0" vs "0.00"），归一成两位小数
            pay_amount=Decimal(str(r["pay_amount"] or 0)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            pay_orders=int(r["pay_orders"] or 0),
            ad_spend_total=r["ad_spend_total"],
            zhitongche_spend=r["zhitongche_spend"],
            yinli_spend=r["yinli_spend"],
        )

    async def upsert_manual(
        self,
        tenant_id: UUID,
        day: date,
        payload: StoreDailyManualUpdate,
        user: User,
    ) -> StoreDaily:
        """写手填层（设计 2.4）：只写传了的字段，传 ``null`` 就清空（``model_fields_set``）。

        日期先判再写：晚于今天 / 早于 2020-01-01 → 422。审计记改前改后（同一事务）；
        手填层读取时 LEFT JOIN，录完立即生效，不清汇总覆盖。
        """
        if day > get_today():
            raise StoreDailyFutureDateError()
        if day < _EARLIEST_MANUAL_DAY:
            raise StoreDailyDateTooEarlyError()
        set_fields = payload.model_dump(include=payload.model_fields_set)
        # 改前：锁住已有行（只取列，不进 identity map）；还没有行 → None
        before_row = (
            await self._session.execute(
                select(*(getattr(StoreDaily, f) for f in _MANUAL_FIELDS))
                .where(StoreDaily.tenant_id == tenant_id, StoreDaily.date == day)
                .with_for_update()
            )
        ).one_or_none()
        stmt = (
            pg_insert(StoreDaily)
            .values(tenant_id=tenant_id, date=day, **set_fields)
            .on_conflict_do_update(
                index_elements=["tenant_id", "date"],
                set_={**set_fields, "updated_at": func.now()},
            )
            .returning(StoreDaily)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await self._audit.log(
            action="report.store_daily.update",
            resource="store_daily",
            resource_id=row.id,
            before=None if before_row is None else _manual_snapshot(before_row._mapping),
            after={
                "date": str(day),
                **_manual_snapshot({f: getattr(row, f) for f in _MANUAL_FIELDS}),
            },
            user_id=user.id,
        )
        await self._session.commit()
        return row


__all__ = ["StoreDailyService"]
