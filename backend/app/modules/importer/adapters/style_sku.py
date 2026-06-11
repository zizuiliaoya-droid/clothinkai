"""U06b 商品/SKU 导入适配器（StyleSkuImportAdapter）。

按 nfr-design-patterns.md P-U06b-01 实现：一行 = 一个 SKU + 其所属 Style。

关键设计：
- **不经 U02 Service**（Service 自带 commit/audit/权限，与 runner per-row 事务边界 FB-C 冲突，
  且 Celery worker 无 HTTP User）→ 直接用 StyleRepository / SkuRepository
- **不自行 commit**：复用 runner 传入的 session（runner 持有 per-row 事务 + SET LOCAL，NF-1）
- **style 复用不覆盖**：get_by_code 命中仅用 id（BR-U06b-31）
- **sku 幂等**：SkuRepository.upsert_atomic（ON CONFLICT，复用 U02 P-U02-03）
- **Decimal 禁 float**（BR-U06b-13）+ brand 软关联（BR-U06b-33）
- mapping=None 回退内置默认映射（domain-entities §4）
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.models import Brand, Style
from app.modules.product.repository import SkuRepository, StyleRepository

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "款式编码", "target_field": "style_code", "type": "str"},
    {"source_col": "款式名称", "target_field": "style_name", "type": "str"},
    {"source_col": "类目", "target_field": "category", "type": "str"},
    {"source_col": "品牌编码", "target_field": "brand_code", "type": "str"},
    {"source_col": "季节", "target_field": "season", "type": "str"},
    {"source_col": "SKU编码", "target_field": "sku_code", "type": "str"},
    {"source_col": "颜色", "target_field": "color", "type": "str"},
    {"source_col": "尺码", "target_field": "size", "type": "str"},
    {"source_col": "成本价", "target_field": "cost_price", "type": "decimal"},
    {"source_col": "采购价", "target_field": "purchase_price", "type": "decimal"},
    {"source_col": "吊牌价", "target_field": "base_price", "type": "decimal"},
    {"source_col": "货源类型", "target_field": "sourcing_type", "type": "str"},
]

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("style_code", "款式编码"),
    ("style_name", "款式名称"),
    ("category", "类目"),
    ("sku_code", "SKU编码"),
    ("color", "颜色"),
    ("size", "尺码"),
)
_DECIMAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("cost_price", "成本价"),
    ("purchase_price", "采购价"),
    ("base_price", "吊牌价"),
)
_SOURCING = frozenset({"自产", "采购", "代发"})
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("style_code", 64),
    ("style_name", 255),
    ("category", 32),
    ("sku_code", 64),
    ("color", 64),
    ("size", 32),
)


def _to_decimal(raw: Any) -> Decimal | str | None:
    """去千分位 + Decimal（禁 float）。非法值保留原串供 validate 检出。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return str(raw)  # 非 Decimal → validate 报错


class StyleSkuImportAdapter:
    """manual_style_sku 导入适配器（一行 → Style 复用/创建 + Sku upsert）。"""

    source: str = "manual_style_sku"
    target_table: str = "style+sku"

    # ----------------------- parse_row（纯函数）----------------------- #

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        """按 mapping（或内置默认）把表头映射为目标字段 + 类型转换。"""
        if mapping is not None:
            columns = mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
        else:
            columns = _DEFAULT_COLUMNS

        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            target = col["target_field"]
            if col.get("type") == "decimal":
                parsed[target] = _to_decimal(raw)
            else:
                parsed[target] = (
                    str(raw).strip() if raw not in (None, "") else None
                )
        return parsed

    # ----------------------- validate（纯函数）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空=通过）。"""
        errs: list[str] = []
        for field, label in _REQUIRED:
            if not parsed.get(field):
                errs.append(f"{label}不能为空")
        for field, label in _DECIMAL_FIELDS:
            value = parsed.get(field)
            if value is not None and (
                not isinstance(value, Decimal) or value < 0
            ):
                errs.append(f"{label}必须为非负数字")
        sourcing = parsed.get("sourcing_type")
        if sourcing and sourcing not in _SOURCING:
            errs.append("货源类型必须为 自产/采购/代发 之一")
        for field, max_len in _MAX_LEN:
            value = parsed.get(field)
            if value and len(value) > max_len:
                errs.append(f"{field} 超过长度上限 {max_len}")
        return errs


    # ----------------------- upsert（复用 runner session，不 commit）----------------------- #

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """style get-or-create + sku upsert_atomic。返回 (sku_id, is_inserted)。

        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        style + sku 同 per-row 事务原子（sku 失败 → 整行回滚含新建 style）。
        """
        styles = StyleRepository(session)
        skus = SkuRepository(session)

        # 1) style 复用（不覆盖）/ 创建（BR-U06b-31）
        style = await styles.get_by_code(parsed["style_code"])
        if style is None:
            brand_id = await self._resolve_brand(
                session, tenant_id, parsed.get("brand_code")
            )
            style = Style(
                style_code=parsed["style_code"],
                style_name=parsed["style_name"],
                category=parsed["category"],
                season=parsed.get("season"),
                brand_id=brand_id,
                owner_id=actor_id,
                design_status="大货",
            )
            styles.add(style)
            await session.flush()  # 拿 style.id；UNIQUE 冲突 → 行 failed（runner 捕获）

        # 2) sku upsert（ON CONFLICT，复用 U02 P-U02-03）
        sku, is_inserted = await skus.upsert_atomic(
            tenant_id=tenant_id,
            values={
                "sku_code": parsed["sku_code"],
                "style_id": style.id,
                "color": parsed["color"],
                "size": parsed["size"],
                "cost_price": parsed.get("cost_price"),
                "purchase_price": parsed.get("purchase_price"),
                "base_price": parsed.get("base_price"),
                "sourcing_type": parsed.get("sourcing_type") or "自产",
            },
        )
        return sku.id, is_inserted

    async def _resolve_brand(
        self, session: AsyncSession, tenant_id: UUID, brand_code: str | None
    ) -> UUID | None:
        """brand_code → brand_id（软关联，查不到 None，不报错，BR-U06b-33）。"""
        if not brand_code:
            return None
        stmt = select(Brand.id).where(
            Brand.tenant_id == tenant_id, Brand.brand_code == brand_code
        )
        return (await session.execute(stmt)).scalar_one_or_none()


def register() -> None:
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(StyleSkuImportAdapter())


__all__ = ["StyleSkuImportAdapter", "register"]
