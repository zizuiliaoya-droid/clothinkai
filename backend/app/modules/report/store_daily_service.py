"""U14 StoreDailyService（店铺数据看板聚合 + 手动字段 upsert）。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func
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
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.report.work_progress_models import StoreDaily


class StoreDailyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StoreDailyRepository(session)
        self._summary = SummaryReadRepository(session)
        self._audit = AuditService(session)

    async def get_dashboard(
        self, tenant_id: UUID, time_range: tuple[date, date]
    ) -> list[StoreDailyRow]:
        """店铺日数据。区间被汇总表完整覆盖就读汇总表，否则实时。

        手填的 3 个广告消耗两条路径都是读取时 LEFT JOIN ``store_daily``，填完立即生效；
        extra（千牛导出的其余几十列）汇总表不存，始终实时。
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
        extra_by_date = await self._aggregate_extra(tenant_id, time_range[0], time_range[1])
        result = []
        for r in rows:
            row = self._to_row(r)
            row.extra = extra_by_date.get(str(r["date"]), {})
            result.append(row)
        return result

    async def _aggregate_extra(
        self, tenant_id: UUID, date_from: date, date_to: date
    ) -> dict[str, dict[str, Any]]:
        """按日 SUM qianniu_daily.extra 的数值列（对齐 final.xlsx 店铺数据 24 列）。"""
        from collections import defaultdict
        from decimal import Decimal, InvalidOperation

        from sqlalchemy import text

        sql = text(
            "SELECT date, extra FROM qianniu_daily "
            "WHERE tenant_id = :t AND date BETWEEN :f AND :to AND extra IS NOT NULL"
        )
        rows = (
            await self._session.execute(sql, {"t": str(tenant_id), "f": date_from, "to": date_to})
        ).all()
        agg: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        # 非指标列（ID/文本类）不参与按日求和
        skip = {
            "统计日期",
            "日期",
            "商品ID",
            "主商品ID",
            "货号",
            "商品名称",
            "商品简称",
            "商商品简称称",
            "商品类型",
            "商品状态",
            "商品标签",
        }
        for d, extra in rows:
            if not isinstance(extra, dict):
                continue
            day = str(d)
            for k, v in extra.items():
                if k in skip or v is None or v == "":
                    continue
                try:
                    num = Decimal(str(v).replace(",", "").replace("%", "").strip())
                except (InvalidOperation, ValueError):
                    continue
                agg[day][k] += num
        # Decimal → str（保留两位以内）
        return {
            day: {k: format(val, "f") for k, val in fields.items()} for day, fields in agg.items()
        }

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
