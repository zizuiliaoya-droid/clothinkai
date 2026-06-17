"""U16 拍单 / 刷单导入适配器（OrderAdjustmentImportAdapter）。

一行 = 一条 order_adjustment（INSERT-only）。两个 source：
- ``manual_tao_order``  → order_type=拍单
- ``manual_brush_order``→ order_type=刷单（额外支持「是否剔除ROI」列）

关键设计（对齐 style_sku / promotion adapter 范式）：
- **不经 Service**（Service 自带 commit/audit/权限，与 runner per-row 事务 FB-C 冲突）→ 直接 ORM
- **不自行 commit**：复用 runner 传入 session（NF-1 per-row SET LOCAL，tenant_id 由 before_flush 注入）
- **款号软关联**：按 style_code 反查 style_id，查不到则 NULL（不阻塞）
- **Decimal 禁 float**；列名兼容各平台导出变体（aliases）
- 列对齐 final.xlsx「拍单」sheet：销售类型/日期/订单号/博主ID|微信ID/款号/金额
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.repository import StyleRepository

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

# 内置默认映射（中文表头 → 目标字段 + aliases 兼容平台导出变体）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "日期", "target_field": "order_date", "type": "date",
     "aliases": ["拍单日期", "刷单日期", "下单日期", "订单日期"]},
    {"source_col": "订单号", "target_field": "order_no", "type": "str",
     "aliases": ["订单编号", "单号"]},
    {"source_col": "博主ID", "target_field": "blogger_identifier", "type": "str",
     "aliases": ["微信ID", "博主标识", "博主ID/微信ID", "博主"]},
    {"source_col": "款号", "target_field": "style_code", "type": "str",
     "aliases": ["款式编码", "货号", "商品编码"]},
    {"source_col": "金额", "target_field": "amount", "type": "decimal",
     "aliases": ["拍单金额", "刷单金额", "订单金额", "成交金额"]},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
    {"source_col": "是否剔除ROI", "target_field": "exclude_from_roi", "type": "bool",
     "aliases": ["ROI剔除", "剔除ROI", "是否剔除"]},
]

_TRUE_TOKENS = frozenset(
    {"1", "true", "t", "y", "yes", "是", "剔除", "排除", "需剔除"}
)


def _to_date(raw: Any) -> date | str | None:
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return s  # 非法保留原串供 validate 检出


def _to_decimal(raw: Any) -> Decimal | str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").replace("¥", "").strip())
    except InvalidOperation:
        return str(raw)


def _to_bool(raw: Any) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUE_TOKENS


class OrderAdjustmentImportAdapter:
    """拍单/刷单导入适配器（一行 → 一条 order_adjustment，INSERT-only）。"""

    target_table: str = "order_adjustment"

    def __init__(self, source: str, order_type: str) -> None:
        self.source = source
        self.order_type = order_type

    # ----------------------- parse_row（纯函数）----------------------- #

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        columns = (
            mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
            if mapping is not None
            else _DEFAULT_COLUMNS
        )
        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            if raw in (None, ""):
                for alias in col.get("aliases", []):
                    if row.get(alias) not in (None, ""):
                        raw = row.get(alias)
                        break
            tgt = col["target_field"]
            col_type = col.get("type", "str")
            if col_type == "decimal":
                parsed[tgt] = _to_decimal(raw)
            elif col_type == "date":
                parsed[tgt] = _to_date(raw)
            elif col_type == "bool":
                parsed[tgt] = _to_bool(raw)
            else:
                parsed[tgt] = str(raw).strip() if raw not in (None, "") else None
        return parsed

    # ----------------------- validate（纯函数）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        errs: list[str] = []
        amount = parsed.get("amount")
        if amount is None:
            errs.append("金额不能为空")
        elif not isinstance(amount, Decimal) or amount < 0:
            errs.append("金额必须为非负数字")
        order_date = parsed.get("order_date")
        if order_date is not None and not isinstance(order_date, date):
            errs.append("日期格式非法（应为 YYYY-MM-DD）")
        return errs

    # ----------------------- upsert（INSERT-only，复用 runner session）----------------------- #

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """款号软关联 → INSERT order_adjustment。返回 (id, True)。

        不自行 commit（runner 持有 per-row 事务边界，FB-C）；tenant_id 显式传入
        （runner per-row 走 bypass_rls，before_flush 钩子不注入，须手动赋值，NF-1）。
        """
        style_id: UUID | None = None
        if parsed.get("style_code"):
            style = await StyleRepository(session).get_by_code(
                parsed["style_code"]
            )
            if style is not None:
                style_id = style.id

        row = OrderAdjustment(
            tenant_id=tenant_id,
            order_type=self.order_type,
            order_date=parsed.get("order_date"),
            order_no=parsed.get("order_no"),
            blogger_identifier=parsed.get("blogger_identifier"),
            style_id=style_id,
            amount=parsed["amount"],
            exclude_from_roi=bool(parsed.get("exclude_from_roi")),
            remark=parsed.get("remark"),
        )
        session.add(row)
        await session.flush()  # 拿 id；约束冲突 → 行 failed（runner 捕获）
        return row.id, True  # INSERT-only


def register() -> None:
    """注册拍单/刷单两个 source（双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(
        OrderAdjustmentImportAdapter("manual_tao_order", "拍单")
    )
    ImportAdapterRegistry.register(
        OrderAdjustmentImportAdapter("manual_brush_order", "刷单")
    )


__all__ = ["OrderAdjustmentImportAdapter", "register"]
