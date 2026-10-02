"""U13 千牛商品日报导入适配器（QianniuImportAdapter）。

source=qianniu → qianniu_daily。
- find_by_platform_id 反查 platform_product → 填 platform_product_id
- 未匹配 → DataQualityIssue(warning) + platform_product_id=NULL（不阻塞）
- UNIQUE(tenant, platform_id_snapshot, date) ON CONFLICT 幂等
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
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.platform_product_service import PlatformProductService

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

_PLATFORM = "千牛"
_DEFAULT_COLUMNS = [
    {"source_col": "商品ID", "target_field": "platform_id", "type": "str"},
    {"source_col": "统计日期", "target_field": "date", "type": "date"},
    {"source_col": "商品访客数", "target_field": "visitors", "type": "int"},
    {"source_col": "支付金额", "target_field": "pay_amount", "type": "decimal"},
    {"source_col": "支付件数", "target_field": "pay_orders", "type": "int"},
]

# 退款与加购：报表要用，但不是每份导出都带这两列，缺了也不该让整行失败。
#
# 不放进 _DEFAULT_COLUMNS：那份清单会被租户的自定义映射**整体替换**，放进去的话一旦有人
# 配了映射，这两列就静默丢了 —— 报表又回到恒为 0 却不报错的状态（055 修的正是这个）。
# 所以这里按生意参谋的标准表头兜底；映射里显式配了同名目标字段时以映射为准。
_REFUND_HEADER = "成功退款金额"
_ADD_CART_HEADER = "商品加购件数"


def _to_optional_decimal(raw: Any) -> Decimal | None:
    """宽松解析：去空白与千分位；空串、"-"、认不出的值一律 None。

    与 055 回填 SQL 同一规则。不编造 0 —— 「没有这列」和「退款为 0」要分得开。
    """
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _to_optional_int(raw: Any) -> int | None:
    value = _to_optional_decimal(raw)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


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


class QianniuImportAdapter:
    source: str = "qianniu"
    target_table: str = "qianniu_daily"
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
        # 映射里配了就用映射的结果（同样过一遍宽松解析，"-" 不能变成写库失败），
        # 没配就按标准表头取
        parsed["refund_amount"] = _to_optional_decimal(
            parsed["refund_amount"] if "refund_amount" in parsed else row.get(_REFUND_HEADER)
        )
        parsed["add_cart_count"] = _to_optional_int(
            parsed["add_cart_count"] if "add_cart_count" in parsed else row.get(_ADD_CART_HEADER)
        )
        # 保留整行原始列到 extra（对齐 final.xlsx 千牛 38 列；typed 字段供聚合用）
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
                source="qianniu",
                severity="warning",
                message=f"未匹配 platform_product: {platform_id}",
                entity_type="platform_product",
                entity_ref=platform_id,
            )
        await session.execute(
            text(
                "INSERT INTO qianniu_daily (id, tenant_id, platform_product_id, "
                "platform_id_snapshot, date, visitors, pay_amount, pay_orders, "
                "refund_amount, add_cart_count, extra, created_at, updated_at) "
                "VALUES (:id, :t, :ppid, :pid, :d, :v, :amt, :ord, :refund, :cart, "
                "CAST(:extra AS JSONB), NOW(), NOW()) "
                "ON CONFLICT (tenant_id, platform_id_snapshot, date) DO UPDATE SET "
                "visitors=EXCLUDED.visitors, pay_amount=EXCLUDED.pay_amount, "
                "pay_orders=EXCLUDED.pay_orders, refund_amount=EXCLUDED.refund_amount, "
                "add_cart_count=EXCLUDED.add_cart_count, extra=EXCLUDED.extra, "
                "platform_product_id=EXCLUDED.platform_product_id, updated_at=NOW()"
            ),
            {
                "id": str(uuid4()),
                "t": str(tenant_id),
                "ppid": str(ppid) if ppid else None,
                "pid": platform_id,
                "d": parsed["date"],
                "v": parsed.get("visitors"),
                "amt": parsed.get("pay_amount"),
                "ord": parsed.get("pay_orders"),
                "refund": parsed.get("refund_amount"),
                "cart": parsed.get("add_cart_count"),
                "extra": json.dumps(parsed.get("extra") or {}, ensure_ascii=False),
            },
        )
        return uuid4(), True


def register() -> None:
    ImportAdapterRegistry.register(QianniuImportAdapter())


__all__ = ["QianniuImportAdapter", "register"]
