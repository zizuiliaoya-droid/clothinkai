"""仅画像来源使用分组事务；业务记录与该组全部行结果共同提交。"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.importer.adapters.blogger_audience import BloggerAudienceImportAdapter
from app.modules.importer.models import FieldMapping
from app.modules.importer.outcome import RowOutcome


async def process_audience_groups(
    rows: list[tuple[int, dict[str, Any]]],
    mapping: FieldMapping | None,
    *,
    batch_id: UUID,
    tenant_id: UUID,
    actor_id: UUID | None,
    app_session: Callable[[], AsyncSession],
    bypass_session: Callable[[], AsyncSession],
) -> list[RowOutcome | None]:
    # 复用原任务的 session 工厂与行结果写入；同样支持测试注入的 session 工厂。
    from app.tasks import import_tasks as runner

    adapter = BloggerAudienceImportAdapter()
    groups: dict[tuple[str, ...], list[tuple[int, dict[str, Any], dict[str, Any]]]] = {}
    for row_number, raw in rows:
        parsed = adapter.parse_row(raw, mapping)
        groups.setdefault(adapter.group_key(parsed), []).append((row_number, raw, parsed))
    outcomes: list[RowOutcome | None] = []
    for group in groups.values():
        try:
            async with app_session() as session:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
                )
                outcome = await adapter.save_group(
                    [(number, parsed) for number, _, parsed in group],
                    session=session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    batch_id=batch_id,
                )
                for number, raw, _ in group:
                    await runner._upsert_job(
                        session,
                        batch_id=batch_id,
                        tenant_id=tenant_id,
                        row_number=number,
                        row=raw,
                        status=runner._JOB_STATUS[outcome.kind],
                        error_detail=None,
                        target_resource_id=outcome.resource_id,
                        notes=runner._job_notes(outcome),
                    )
                await session.commit()
            outcomes.extend([outcome] * len(group))
        except Exception as exc:
            detail = f"同组数据行 {', '.join(str(n) for n, _, _ in group)} 全部失败：{runner._row_error_detail(exc)}"
            # 失败结果也一次提交，避免仅失败行重试时丢失组的一部分。
            async with bypass_session() as session:
                for number, raw, _ in group:
                    await runner._upsert_job(
                        session,
                        batch_id=batch_id,
                        tenant_id=tenant_id,
                        row_number=number,
                        row=raw,
                        status="failed",
                        error_detail=detail,
                        target_resource_id=None,
                    )
                await session.commit()
            outcomes.extend([None] * len(group))
    return outcomes
