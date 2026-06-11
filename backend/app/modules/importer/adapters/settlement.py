"""U06e 结算导入适配器（SettlementImportAdapter）。

按 nfr-design-patterns.md P-U06e-01 实现：历史结算迁移，一行 = 一条新 Settlement（INSERT-only）。

关键设计（settlement 是特例）：
- **INSERT-only**：settlement 由 U04 事件（SettlementRequested）创建，本适配器用于历史/遗留迁移。
- **promotion 派生**：仅文件提供 promotion 推广编号；blogger_id/style_id/pr_id 从 promotion 派生
  （保证一致，不让文件提供）。
- **UNIQUE(tenant_id, promotion_id) 一对一**：重复 promotion → IntegrityError → RowValidationError
  （FB3 财务记录永久不可替换，不覆盖既有）。
- **settlement_no**：next_settlement_sequence（U05 FB2 原子序列）+ format_settlement_no
  （tenant_code 实例级缓存）。
- **合成 request_event_id = uuid4()**：导入无真实事件，满足 UNIQUE(request_event_id) 约束。
- **不触发事件**：不调 event_bus.dispatch（导入是数据迁移，区别 U05 service 的 SettlementPaid）。
- **不经 U05 Service**：避免 commit/audit/事件/状态机校验，直接用 Repository（FB-C）。
- **不自行 commit**：复用 runner 传入 session（NF-1 per-row SET LOCAL）。
- date（_to_date）+ Decimal（_to_decimal 禁 float）解析；status ∈ 5 枚举校验。
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import Tenant
from app.modules.finance.domain import format_settlement_no
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.models import Settlement
from app.modules.finance.repository import SettlementRepository
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.promotion.repository import PromotionRepository

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

# settlement_status 5 枚举值（待核查/待付款/待财务付款/已付款/已驳回）
_VALID_STATUS: frozenset[str] = frozenset(s.value for s in SettlementStatus)

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段，9 列，domain-entities §4）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "推广编号", "target_field": "promotion_internal_code", "type": "str"},
    {"source_col": "结算日期", "target_field": "settlement_date", "type": "date"},
    {"source_col": "金额", "target_field": "amount", "type": "decimal"},
    {"source_col": "总金额", "target_field": "total_amount", "type": "decimal"},
    {"source_col": "付款金额", "target_field": "payment_amount", "type": "decimal"},
    {"source_col": "付款日期", "target_field": "payment_date", "type": "date"},
    {"source_col": "结算状态", "target_field": "settlement_status", "type": "str"},
    {"source_col": "笔记标题", "target_field": "note_title", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("promotion_internal_code", "推广编号"),
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


class SettlementImportAdapter:
    """manual_settlement 导入适配器（一行 → 一条新 Settlement，历史迁移 INSERT-only）。"""

    source: str = "manual_settlement"
    target_table: str = "settlement"

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
        """返回错误描述列表（空=通过）。promotion FK 存在性在 upsert 阶段校验。"""
        errs: list[str] = []
        for field, label in _REQUIRED:
            if not parsed.get(field):
                errs.append(f"{label}不能为空")

        # amount / total_amount 必填 Decimal ≥ 0
        for field, label in (("amount", "金额"), ("total_amount", "总金额")):
            value = parsed.get(field)
            if value is None:
                errs.append(f"{label}不能为空")
            elif not isinstance(value, Decimal) or value < 0:
                errs.append(f"{label}必须为非负数字")

        # payment_amount 可选 Decimal ≥ 0
        pay = parsed.get("payment_amount")
        if pay is not None and (not isinstance(pay, Decimal) or pay < 0):
            errs.append("付款金额必须为非负数字")

        # settlement_date 必填 date
        sett_date = parsed.get("settlement_date")
        if sett_date is None:
            errs.append("结算日期不能为空")
        elif not isinstance(sett_date, date):
            errs.append("结算日期格式错误（应为 YYYY-MM-DD）")

        # payment_date 可选 date
        pay_date = parsed.get("payment_date")
        if pay_date is not None and not isinstance(pay_date, date):
            errs.append("付款日期格式错误（应为 YYYY-MM-DD）")

        # settlement_status 可选，但若提供须 ∈ 5 枚举
        status = parsed.get("settlement_status")
        if status and status not in _VALID_STATUS:
            errs.append(
                "结算状态必须为 待核查/待付款/待财务付款/已付款/已驳回 之一"
            )

        note_title = parsed.get("note_title")
        if note_title and isinstance(note_title, str) and len(note_title) > 255:
            errs.append("note_title 超过长度上限 255")
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
        """promotion 派生 → settlement_no 生成 → INSERT settlement。返回 (settlement.id, True)。

        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        promotion 缺失 / UNIQUE(promotion_id) 冲突 → RowValidationError（runner 捕获 → import_job.failed）。
        派生 + sequence + INSERT 同 per-row 事务原子；不触发事件。
        """
        promotions = PromotionRepository(session)
        settlements = SettlementRepository(session)

        # 1) promotion 派生（不让文件提供 blogger/style/pr）
        promo = await promotions.get_by_internal_code(
            parsed["promotion_internal_code"]
        )
        if promo is None:
            raise RowValidationError(
                f"推广编号 {parsed['promotion_internal_code']} 不存在"
            )

        # 2) settlement_no（tenant_code 缓存 + FB2 原子序列）+ 合成 event_id
        tenant_code = await self._get_tenant_code(session, tenant_id)
        sequence = await settlements.next_settlement_sequence(
            tenant_id=tenant_id, date_key=parsed["settlement_date"]
        )  # SequenceOverflowError → runner 捕获 → failed
        settlement_no = format_settlement_no(
            tenant_code=tenant_code,
            date_key=parsed["settlement_date"],
            sequence=sequence,
        )

        # 3) INSERT settlement（不触发事件；FB3 不覆盖既有）
        settlement = Settlement(
            promotion_id=promo.id,
            blogger_id=promo.blogger_id,  # 派生
            style_id=promo.style_id,  # 派生
            pr_id=promo.pr_id,  # 派生
            settlement_no=settlement_no,
            amount=parsed["amount"],
            total_amount=parsed["total_amount"],
            payment_amount=parsed.get("payment_amount"),
            payment_date=parsed.get("payment_date"),
            settlement_status=parsed.get("settlement_status") or "待核查",
            note_title=parsed.get("note_title"),
            remark=parsed.get("remark"),
            request_event_id=uuid4(),  # 合成（导入无真实事件）
        )
        settlements.add(settlement)
        try:
            await session.flush()
        except IntegrityError as exc:
            # UNIQUE(tenant_id, promotion_id) 冲突 → 该 promotion 已有 settlement（FB3 不覆盖）
            raise RowValidationError(
                "该推广已有结算单（不可重复，FB3）"
            ) from exc
        return settlement.id, True  # INSERT-only → is_inserted 恒 True

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
    ImportAdapterRegistry.register(SettlementImportAdapter())


__all__ = ["SettlementImportAdapter", "register"]
