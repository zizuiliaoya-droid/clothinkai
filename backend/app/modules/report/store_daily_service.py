"""U14 StoreDailyService（店铺数据看板聚合 + 手动字段 upsert）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import report_query_duration_seconds
from app.modules.auth.models import User
from app.modules.report.advanced_repository import StoreDailyRepository
from app.modules.report.advanced_schemas import (
    StoreDailyManualUpdate,
    StoreDailyRow,
)
from app.modules.report.domain import bucket_start
from app.modules.report.extra_metrics import aggregate_extra
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.report.work_progress_models import StoreDaily


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
        set_fields = {
            k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None
        }
        stmt = (
            pg_insert(StoreDaily)
            .values(tenant_id=tenant_id, date=day, **set_fields)
            .on_conflict_do_update(
                index_elements=["tenant_id", "date"],
                set_={**set_fields, "updated_at": func.now()},
            )
            .returning(StoreDaily)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await self._audit.log(
            action="report.store_daily.update",
            resource="store_daily",
            resource_id=row.id,
            after={"date": str(day), **{k: str(v) for k, v in set_fields.items()}},
            user_id=user.id,
        )
        await self._session.commit()
        return row


__all__ = ["StoreDailyService"]
