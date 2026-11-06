"""画像长表：同账号、维度、日期的行作为一次采集提交。"""

import re
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.audience_schemas import DIMENSION_LABELS, AudienceEntry, AudienceInput
from app.modules.blogger.audience_service import save_audience
from app.modules.blogger.enums import Platform
from app.modules.blogger.models import Blogger
from app.modules.importer.adapters.blogger import _cell
from app.modules.importer.domain import TargetSpec, builtin_columns_from_targets
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.models import FieldMapping
from app.modules.importer.outcome import RowKind, RowOutcome
from app.modules.importer.registry import ImportAdapterRegistry

DIMENSIONS = {
    **{key: key for key in DIMENSION_LABELS},
    **{label: key for key, label in DIMENSION_LABELS.items()},
}


def excel_percent_value(value: Any, number_format: str) -> Any:
    """只按 Excel 的真实百分号格式换算；引号/反斜杠中的百分号只是文字。"""
    number_format = re.sub(r'"[^"]*"|\\.', "", number_format)
    if (
        isinstance(value, int | float | Decimal)
        and not isinstance(value, bool)
        and "%" in number_format
    ):
        return f"{Decimal(str(value)) * 100}%"
    return value


class BloggerAudienceImportAdapter:
    source = "manual_blogger_audience"
    target_table = "blogger_audience_capture"
    mapping_targets: ClassVar[tuple[TargetSpec, ...]] = tuple(
        TargetSpec(field, label, "str", label, required=required)
        for field, label, required in (
            ("platform", "平台", True),
            ("xiaohongshu_id", "账号", True),
            ("dimension", "维度", True),
            ("label", "原始标签", True),
            ("percentage", "占比", False),
            ("tgi", "TGI", False),
            ("collected_on", "采集日期", True),
            ("source", "采集来源", True),
            ("completeness", "完整性", True),
            ("estimated", "是否估计", True),
            ("tgi_source", "TGI来源", False),
        )
    )

    def builtin_columns(self) -> list[dict[str, Any]]:
        return builtin_columns_from_targets(self.mapping_targets)

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        columns = mapping.mapping_config["columns"] if mapping else self.builtin_columns()
        parsed = {c["target_field"]: _cell(row, c) for c in columns}
        parsed = {k: v.strip() if isinstance(v, str) else v for k, v in parsed.items()}
        aliases: dict[str, dict[str, Any]] = {
            "dimension": DIMENSIONS,
            "completeness": {"完整": "complete", "部分": "partial"},
            "estimated": {"是": True, "否": False},
        }
        for key, values in aliases.items():
            value = parsed.get(key)
            if isinstance(value, str):
                parsed[key] = values.get(value, value)
        return parsed

    def group_key(self, parsed: dict[str, Any]) -> tuple[str, ...]:
        day = parsed.get("collected_on", "")
        try:
            day = TypeAdapter(date).validate_python(day).isoformat()
        except ValueError:
            pass
        return (
            *(str(parsed.get(key, "")) for key in ("platform", "xiaohongshu_id", "dimension")),
            str(day),
        )

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        errors = []
        if parsed.get("platform") not in {p.value for p in Platform}:
            errors.append("请明确填写有效平台")
        if not parsed.get("xiaohongshu_id"):
            errors.append("账号不能为空")
        try:
            AudienceEntry.model_validate(
                {key: parsed.get(key) for key in ("label", "percentage", "tgi")}
            )
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    def payload(self, rows: list[tuple[int, dict[str, Any]]]) -> AudienceInput:
        first = rows[0][1]
        metadata = (
            "dimension",
            "collected_on",
            "source",
            "completeness",
            "estimated",
            "tgi_source",
        )
        for row_number, row in rows:
            errors = self.validate(row)
            if errors:
                raise RowValidationError(f"第 {row_number} 数据行：{'；'.join(errors)}")
            if any(row.get(key) != first.get(key) for key in metadata if key != "collected_on"):
                raise RowValidationError(
                    f"第 {row_number} 数据行的采集来源、完整性、估计或TGI来源与同组不一致"
                )
        return AudienceInput.model_validate(
            {
                **{key: first.get(key) for key in metadata},
                "entries": [
                    {key: row.get(key) for key in ("label", "percentage", "tgi")} for _, row in rows
                ],
            }
        )

    async def save_group(
        self,
        rows: list[tuple[int, dict[str, Any]]],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
        batch_id: UUID | None,
    ) -> RowOutcome:
        payload = self.payload(rows)
        first = rows[0][1]
        blogger_id = await session.scalar(
            select(Blogger.id).where(
                Blogger.tenant_id == tenant_id,
                Blogger.platform == first["platform"],
                Blogger.xiaohongshu_id == first["xiaohongshu_id"],
                Blogger.is_deleted.is_(False),
            )
        )
        if blogger_id is None:
            raise RowValidationError("未匹配本租户已有博主，请先录入对应平台和账号")
        result = await save_audience(
            session, tenant_id, blogger_id, payload, actor_id, batch_id, [n for n, _ in rows]
        )
        return RowOutcome(
            result.id,
            RowKind.INSERTED if result.inserted else RowKind.SKIPPED,
            warnings=[result.message] if result.inserted and not result.current else [],
        )

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        raise RowValidationError("画像必须按采集分组导入")


def register() -> None:
    ImportAdapterRegistry.register(BloggerAudienceImportAdapter())
