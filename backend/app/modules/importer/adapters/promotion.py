"""U06d 推广导入适配器（PromotionImportAdapter）。

按 nfr-design-patterns.md P-U06d-01 实现：一行 = 一个新 Promotion（INSERT-only）。

关键设计：
- **INSERT-only**：internal_code 系统生成（序列号），文件不提供 → 无法 upsert → 每行建新 promotion
- **2 必需 FK 解析**：style_code→style_id + xiaohongshu_id→blogger_id（缺失 → 行失败）；sku_code 可选 FK
- **internal_code**：next_internal_sequence（U04 FB2 原子）+ format_internal_code（tenant_code 实例级缓存）
- **不经 U04 Service**（Service 自带 commit/audit/重复检测 warning/权限，与 runner per-row 事务 FB-C 冲突）→ 直接用 Repository
- **不自行 commit**：复用 runner 传入 session（NF-1 per-row SET LOCAL）
- date（_to_date）+ Decimal（_to_decimal 禁 float）解析；3 状态走 server_default 初始态
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import Tenant
from app.modules.blogger.repository import BloggerRepository
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.repository import SkuRepository, StyleRepository
from app.modules.promotion.domain import format_internal_code
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import PromotionRepository

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "款式编码", "target_field": "style_code", "type": "str"},
    {"source_col": "SKU编码", "target_field": "sku_code", "type": "str"},
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "报价金额", "target_field": "quote_amount", "type": "decimal"},
    {"source_col": "成本快照", "target_field": "cost_snapshot", "type": "decimal"},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "合作日期", "target_field": "cooperation_date", "type": "date"},
    {
        "source_col": "计划发布日期",
        "target_field": "scheduled_publish_date",
        "type": "date",
    },
    {"source_col": "笔记标题", "target_field": "note_title", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("style_code", "款式编码"),
    ("xiaohongshu_id", "小红书ID"),
)
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("style_code", 64),
    ("sku_code", 64),
    ("xiaohongshu_id", 64),
    ("platform", 16),
    ("note_title", 255),
)


def _to_date(raw: Any) -> date | str | None:
    """date.fromisoformat（YYYY-MM-DD）。非法保留原串供 validate。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return str(raw)


def _to_decimal(raw: Any) -> Decimal | str | None:
    """去千分位 + Decimal（禁 float）。非法保留原串。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        return str(raw)


class PromotionImportAdapter:
    """manual_promotion 导入适配器（一行 → 一个新 Promotion，INSERT-only）。"""

    source: str = "manual_promotion"
    target_table: str = "promotion"

    def __init__(self) -> None:
        # tenant_id → tenant.code 实例级缓存（tenant.code 不可变，worker 跨 batch 复用）
        self._tenant_code_cache: dict[UUID, str] = {}

    # ----------------------- parse_row（纯函数）----------------------- #

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        """按 mapping（或内置默认）映射表头 + 类型转换。"""
        if mapping is not None:
            columns = mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
        else:
            columns = _DEFAULT_COLUMNS

        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            target = col["target_field"]
            col_type = col.get("type", "str")
            if col_type == "decimal":
                parsed[target] = _to_decimal(raw)
            elif col_type == "date":
                parsed[target] = _to_date(raw)
            else:
                parsed[target] = (
                    str(raw).strip() if raw not in (None, "") else None
                )
        return parsed

    # ----------------------- validate（纯函数，不查 FK）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空=通过）。FK 存在性在 upsert 阶段校验。"""
        errs: list[str] = []
        for field, label in _REQUIRED:
            if not parsed.get(field):
                errs.append(f"{label}不能为空")

        quote = parsed.get("quote_amount")
        if quote is None:
            errs.append("报价金额不能为空")
        elif not isinstance(quote, Decimal) or quote < 0:
            errs.append("报价金额必须为非负数字")

        cost = parsed.get("cost_snapshot")
        if cost is not None and (not isinstance(cost, Decimal) or cost < 0):
            errs.append("成本快照必须为非负数字")

        coop = parsed.get("cooperation_date")
        if coop is None:
            errs.append("合作日期不能为空")
        elif not isinstance(coop, date):
            errs.append("合作日期格式错误（应为 YYYY-MM-DD）")

        sched = parsed.get("scheduled_publish_date")
        if sched is not None and not isinstance(sched, date):
            errs.append("计划发布日期格式错误（应为 YYYY-MM-DD）")

        for field, max_len in _MAX_LEN:
            value = parsed.get(field)
            if value and isinstance(value, str) and len(value) > max_len:
                errs.append(f"{field} 超过长度上限 {max_len}")
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
        """FK 解析 → internal_code 生成 → INSERT promotion。返回 (promotion.id, True)。

        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        FK 缺失抛 RowValidationError（runner 捕获 → import_job.failed）。
        FK + sequence + INSERT 同 per-row 事务原子。
        """
        styles = StyleRepository(session)
        bloggers = BloggerRepository(session)
        promotions = PromotionRepository(session)

        # 1) FK 解析（style/blogger 必需 + sku 可选）
        style = await styles.get_by_code(parsed["style_code"])
        if style is None:
            raise RowValidationError(f"款式编码 {parsed['style_code']} 不存在")
        blogger = await bloggers.get_by_xiaohongshu_id(parsed["xiaohongshu_id"])
        if blogger is None:
            raise RowValidationError(f"博主 {parsed['xiaohongshu_id']} 不存在")
        sku_id: UUID | None = None
        if parsed.get("sku_code"):
            sku = await SkuRepository(session).get_by_code(parsed["sku_code"])
            if sku is None:
                raise RowValidationError(f"SKU编码 {parsed['sku_code']} 不存在")
            sku_id = sku.id

        # 2) internal_code 生成（tenant_code 缓存 + FB2 原子序列）
        tenant_code = await self._get_tenant_code(session, tenant_id)
        sequence = await promotions.next_internal_sequence(
            tenant_id=tenant_id, date_key=parsed["cooperation_date"]
        )  # SequenceOverflowError → runner 捕获 → failed
        internal_code = format_internal_code(
            tenant_code=tenant_code,
            cooperation_date=parsed["cooperation_date"],
            sequence=sequence,
        )

        # 3) INSERT promotion（3 状态走 server_default 初始态）
        promotion = Promotion(
            style_id=style.id,
            sku_id=sku_id,
            blogger_id=blogger.id,
            pr_id=actor_id,
            internal_code=internal_code,
            style_code_snapshot=style.style_code,
            style_short_name_snapshot=style.short_name or style.style_name,
            quote_amount=parsed["quote_amount"],
            cost_snapshot=parsed.get("cost_snapshot"),
            platform=parsed.get("platform") or "小红书",
            cooperation_date=parsed["cooperation_date"],
            scheduled_publish_date=parsed.get("scheduled_publish_date"),
            note_title=parsed.get("note_title"),
            remark=parsed.get("remark"),
        )
        promotions.add(promotion)
        await session.flush()
        return promotion.id, True  # INSERT-only → is_inserted 恒 True

    async def _get_tenant_code(
        self, session: AsyncSession, tenant_id: UUID
    ) -> str:
        """tenant.code（实例级缓存；tenant.code 不可变，缓存安全）。"""
        if tenant_id not in self._tenant_code_cache:
            code = (
                await session.execute(
                    select(Tenant.code).where(Tenant.id == tenant_id)
                )
            ).scalar_one_or_none() or ""
            self._tenant_code_cache[tenant_id] = code
        return self._tenant_code_cache[tenant_id]


def register() -> None:
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(PromotionImportAdapter())


__all__ = ["PromotionImportAdapter", "register"]
