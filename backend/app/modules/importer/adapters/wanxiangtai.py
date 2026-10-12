"""U13 万相台广告日报导入适配器（WanxiangtaiImportAdapter）。

source=wanxiangtai → ad_daily。同 qianniu 模式（反查 + 未匹配 issue + UNIQUE upsert）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.collect.data_quality_service import DataQualityService
from app.modules.importer.adapters.lenient_parse import to_optional_int
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.platform_product_service import PlatformProductService

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

_PLATFORM = "万相台"
_DEFAULT_COLUMNS = [
    {"source_col": "主体ID", "target_field": "platform_id", "type": "str"},
    {"source_col": "日期", "target_field": "date", "type": "date"},
    {"source_col": "花费", "target_field": "cost", "type": "decimal"},
    {"source_col": "展现量", "target_field": "impressions", "type": "int"},
    {"source_col": "点击量", "target_field": "clicks", "type": "int"},
    {"source_col": "总成交金额", "target_field": "gmv", "type": "decimal"},
]

# 站内加购（062）：投产报表的逐日配对加购要用。同千牛 055 的理由不放进 _DEFAULT_COLUMNS
# —— 那份清单会被租户映射整体替换，放进去一配映射就静默丢了。按标准表头兜底，映射里
# 显式配了 add_cart_count 时以映射为准；宽松解析同 062 回填 SQL（lenient_parse）。
# 「直接购物车数」「间接购物车数」是它的拆分，不读。
_ADD_CART_HEADER = "总购物车数"


def _to_int(raw: Any) -> int | str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return str(raw)


def _to_decimal(raw: Any) -> Decimal | str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        return str(raw)


def _to_date(raw: Any) -> date | str | None:
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return s


class WanxiangtaiImportAdapter:
    source: str = "wanxiangtai"
    target_table: str = "ad_daily"
    # 进报表的业务日期列：导入完成后按这一列的范围刷新报表汇总表（import_tasks._AffectedDates）
    summary_date_field: str | None = "date"

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        columns = (
            mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
            if mapping is not None
            else _DEFAULT_COLUMNS
        )
        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            t = col.get("type", "str")
            tgt = col["target_field"]
            if t == "int":
                parsed[tgt] = _to_int(raw)
            elif t == "decimal":
                parsed[tgt] = _to_decimal(raw)
            elif t == "date":
                parsed[tgt] = _to_date(raw)
            else:
                parsed[tgt] = str(raw).strip() if raw not in (None, "") else None
        # 映射配了就用映射的结果（同样过一遍宽松解析，"-" 不能变成写库失败），没配按标准表头取
        parsed["add_cart_count"] = to_optional_int(
            parsed["add_cart_count"] if "add_cart_count" in parsed else row.get(_ADD_CART_HEADER)
        )
        # 保留整行原始列到 extra（对齐 final.xlsx 站内推广 72 列）
        parsed["extra"] = {
            str(k): (str(v).strip() if v is not None else None)
            for k, v in row.items()
            if str(k).strip()
        }
        return parsed

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        errs: list[str] = []
        if not parsed.get("platform_id"):
            errs.append("商品ID不能为空")
        if not isinstance(parsed.get("date"), date):
            errs.append("日期格式非法")
        return errs

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        platform_id = parsed["platform_id"]
        pp = await PlatformProductService(session).find_by_platform_id(_PLATFORM, platform_id)
        ppid = pp.id if pp else None
        if pp is None:
            await DataQualityService(session).record(
                source="wanxiangtai",
                severity="warning",
                message=f"未匹配 platform_product: {platform_id}",
                entity_type="platform_product",
                entity_ref=platform_id,
            )
        await session.execute(
            text(
                "INSERT INTO ad_daily (id, tenant_id, platform_product_id, "
                "platform_id_snapshot, date, cost, impressions, clicks, gmv, add_cart_count, "
                "extra, created_at, updated_at) "
                "VALUES (:id, :t, :ppid, :pid, :d, :cost, :imp, :clk, :gmv, :cart, "
                "CAST(:extra AS JSONB), NOW(), NOW()) "
                "ON CONFLICT (tenant_id, platform_id_snapshot, date) DO UPDATE SET "
                "cost=EXCLUDED.cost, impressions=EXCLUDED.impressions, "
                "clicks=EXCLUDED.clicks, gmv=EXCLUDED.gmv, "
                "add_cart_count=EXCLUDED.add_cart_count, extra=EXCLUDED.extra, "
                "platform_product_id=EXCLUDED.platform_product_id, updated_at=NOW()"
            ),
            {
                "id": str(uuid4()),
                "t": str(tenant_id),
                "ppid": str(ppid) if ppid else None,
                "pid": platform_id,
                "d": parsed["date"],
                "cost": parsed.get("cost"),
                "imp": parsed.get("impressions"),
                "clk": parsed.get("clicks"),
                "gmv": parsed.get("gmv"),
                "cart": parsed.get("add_cart_count"),
                "extra": json.dumps(parsed.get("extra") or {}, ensure_ascii=False),
            },
        )
        return uuid4(), True


def register() -> None:
    ImportAdapterRegistry.register(WanxiangtaiImportAdapter())


__all__ = ["WanxiangtaiImportAdapter", "register"]
