"""已有博主的作品采集导入，不创建博主或认领关系。"""

from typing import Any, ClassVar
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.enums import Platform
from app.modules.blogger.models import Blogger
from app.modules.blogger.work_schemas import WorkInput
from app.modules.blogger.work_service import save_work
from app.modules.importer.adapters.blogger import _cell
from app.modules.importer.domain import TargetSpec, builtin_columns_from_targets
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.models import FieldMapping
from app.modules.importer.outcome import BatchSeen, ImportRowContext, RowKind, RowOutcome
from app.modules.importer.registry import ImportAdapterRegistry


class BloggerWorkImportAdapter:
    source = "manual_blogger_work"
    target_table = "blogger_work_snapshot"
    mapping_targets: ClassVar[tuple[TargetSpec, ...]] = (
        TargetSpec("platform", "平台", "str", "平台", required=True),
        TargetSpec(
            "xiaohongshu_id", "账号", "str", "账号", aliases=("抖音号", "小红书ID"), required=True
        ),
        TargetSpec("work_id", "作品ID", "str", "作品ID", group="identity:id"),
        TargetSpec("work_url", "作品链接", "str", "作品链接", group="identity:url"),
        TargetSpec("title", "标题", "str", "标题", aliases=("封面文案 / 标题",)),
        TargetSpec(
            "published_on", "发布日期", "str", "发布日期", aliases=("发布时间",), required=True
        ),
        TargetSpec("collected_at", "采集时间", "str", "采集时间", required=True),
        TargetSpec("source", "采集来源", "str", "采集来源", required=True),
        *(
            TargetSpec(f"{field}_count", label, "int", label)
            for field, label in (
                ("like", "点赞"),
                ("comment", "评论"),
                ("share", "分享"),
                ("collect", "收藏"),
                ("play", "播放量"),
            )
        ),
    )

    def builtin_columns(self) -> list[dict[str, Any]]:
        return builtin_columns_from_targets(self.mapping_targets)

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        columns = mapping.mapping_config["columns"] if mapping else self.builtin_columns()
        return {c["target_field"]: _cell(row, c) for c in columns}

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        errors = []
        if parsed.get("platform") not in {p.value for p in Platform}:
            errors.append("请明确填写有效平台")
        if not parsed.get("xiaohongshu_id"):
            errors.append("账号不能为空")
        try:
            self.payload(parsed)
        except PydanticValidationError as exc:
            labels = {s.field: s.label for s in self.mapping_targets}
            errors.extend(
                f"{labels.get(str(e['loc'][0]), str(e['loc'][0])) if e['loc'] else '作品'}: {e['msg']}"
                for e in exc.errors()
            )
        return errors

    @staticmethod
    def payload(parsed: dict[str, Any]) -> WorkInput:
        return WorkInput.model_validate(
            {k: v for k, v in parsed.items() if k not in {"platform", "xiaohongshu_id"}}
        )

    async def upsert_with_context(
        self, parsed: dict[str, Any], *, session: AsyncSession, ctx: ImportRowContext
    ) -> RowOutcome:
        blogger_id = await session.scalar(
            select(Blogger.id).where(
                Blogger.tenant_id == ctx.tenant_id,
                Blogger.platform == parsed["platform"],
                Blogger.xiaohongshu_id == str(parsed["xiaohongshu_id"]).strip(),
                Blogger.is_deleted.is_(False),
            )
        )
        if blogger_id is None:
            raise RowValidationError("未匹配本租户已有博主，请先在博主库录入对应平台和账号")
        row, inserted = await save_work(
            session,
            ctx.tenant_id,
            blogger_id,
            self.payload(parsed),
            ctx.actor_id,
            ctx.batch_id,
            ctx.row_number,
        )
        return RowOutcome(row.id, RowKind.INSERTED if inserted else RowKind.SKIPPED)

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        result = await self.upsert_with_context(
            parsed,
            session=session,
            ctx=ImportRowContext(
                tenant_id,
                self.source,
                None,
                0,
                actor_id,
                BatchSeen(),
            ),
        )
        assert result.resource_id is not None
        return result.resource_id, result.kind == RowKind.INSERTED


def register() -> None:
    ImportAdapterRegistry.register(BloggerWorkImportAdapter())
