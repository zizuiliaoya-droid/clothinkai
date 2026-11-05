"""手工与逐行导入共用作品写入规则；事务由调用方提交。"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from statistics import median
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.blogger.models import Blogger
from app.modules.blogger.work_models import BloggerWorkSnapshot as Snapshot
from app.modules.blogger.work_schemas import WorkHitSummary, WorkInput, WorkItem, WorkPage
from app.modules.importer.models import ImportBatch


async def require_blogger(session: AsyncSession, tenant_id: UUID, blogger_id: UUID) -> Blogger:
    blogger = await session.scalar(
        select(Blogger).where(
            Blogger.id == blogger_id,
            Blogger.tenant_id == tenant_id,
            Blogger.is_deleted.is_(False),
        )
    )
    if blogger is None:
        raise ResourceNotFoundError("博主不存在")
    return blogger


async def save_work(
    session: AsyncSession,
    tenant_id: UUID,
    blogger_id: UUID,
    payload: WorkInput,
    actor_id: UUID | None,
    batch_id: UUID | None = None,
    row_number: int | None = None,
) -> tuple[Snapshot, bool]:
    blogger = await require_blogger(session, tenant_id, blogger_id)
    if (
        batch_id is not None
        and await session.scalar(
            select(ImportBatch.id).where(
                ImportBatch.id == batch_id,
                ImportBatch.tenant_id == tenant_id,
            )
        )
        is None
    ):
        raise ResourceNotFoundError("导入批次不存在")
    values = payload.model_dump()
    row = await session.scalar(
        insert(Snapshot)
        .values(
            **values,
            tenant_id=tenant_id,
            blogger_id=blogger_id,
            platform=blogger.platform,
            work_key=payload.work_key,
            created_by=actor_id,
            import_batch_id=batch_id,
            row_number=row_number,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "blogger_id", "work_key", "collected_at"]
        )
        .returning(Snapshot)
    )
    if row is not None:
        return row, True
    row = (
        await session.scalars(
            select(Snapshot).where(
                Snapshot.tenant_id == tenant_id,
                Snapshot.blogger_id == blogger_id,
                Snapshot.work_key == payload.work_key,
                Snapshot.collected_at == payload.collected_at,
            )
        )
    ).one()
    if any(getattr(row, name) != value for name, value in values.items()):
        raise ValidationError("同一作品同一采集时间已有不同内容，请核对后使用准确采集时间")
    return row, False


def work_item(row: Snapshot, actor_name: str | None = None) -> WorkItem:
    result = WorkItem.model_validate(row)
    result.created_by_name = actor_name
    values = [row.like_count, row.comment_count, row.share_count, row.collect_count]
    if all(v is not None for v in values):
        result.interaction_total = sum(v for v in values if v is not None)
    for metric in ("collect", "share", "comment"):
        count = getattr(row, f"{metric}_count")
        if count is not None and row.like_count:
            setattr(result, f"{metric}_like_ratio", count / row.like_count)
    return result


def summarize_work_hits(rows: Sequence[Snapshot]) -> tuple[WorkHitSummary, frozenset[UUID]]:
    """仅接收当前窗口去重后最近 30 篇，不将缺失点赞补成零。"""
    likes = [Decimal(row.like_count) for row in rows if row.like_count is not None]
    published = [row.published_on for row in rows]
    summary = WorkHitSummary(
        sample_count=len(rows),
        valid_like_count=len(likes),
        published_from=min(published) if published else None,
        published_to=max(published) if published else None,
        status="insufficient_samples",
    )
    if len(likes) < 10:
        return summary, frozenset()
    middle = median(likes)
    threshold = middle * 3
    summary.median_likes = float(middle)
    summary.threshold = float(threshold)
    if middle == 0:
        summary.status = "zero_median"
        return summary, frozenset()
    summary.status = "ready"
    return summary, frozenset(
        row.id for row in rows if row.like_count is not None and row.like_count >= threshold
    )


async def list_works(
    session: AsyncSession,
    tenant_id: UUID,
    blogger_id: UUID,
    *,
    page: int = 1,
    page_size: int = 10,
    date_from: date | None = None,
    date_to: date | None = None,
    snapshot_id: UUID | None = None,
) -> WorkPage:
    await require_blogger(session, tenant_id, blogger_id)
    if date_from and date_to and date_from > date_to:
        raise ValidationError("开始日期不能晚于结束日期")
    base = select(Snapshot).where(
        Snapshot.tenant_id == tenant_id, Snapshot.blogger_id == blogger_id
    )
    if snapshot_id is not None:
        key = await session.scalar(
            select(Snapshot.work_key).where(
                Snapshot.id == snapshot_id,
                Snapshot.tenant_id == tenant_id,
                Snapshot.blogger_id == blogger_id,
            )
        )
        if key is None:
            raise ResourceNotFoundError("作品采集记录不存在")
        base = base.where(Snapshot.work_key == key)
        current = aliased(Snapshot, base.subquery())
    else:
        latest = base.add_columns(
            func.row_number()
            .over(
                partition_by=Snapshot.work_key,
                order_by=Snapshot.collected_at.desc(),
            )
            .label("rank")
        ).subquery()
        current = aliased(Snapshot, latest)
        base = select(current).where(latest.c.rank == 1)
        if date_from:
            base = base.where(current.published_on >= date_from)
        if date_to:
            base = base.where(current.published_on <= date_to)
        current = aliased(Snapshot, base.subquery())
    total = await session.scalar(select(func.count()).select_from(current)) or 0
    order = (
        [current.collected_at.desc(), current.id]
        if snapshot_id
        else [current.published_on.desc(), current.id]
    )
    hit_summary = None
    hit_ids: frozenset[UUID] = frozenset()
    if snapshot_id is None:
        # 与列表共用去重、筛选和排序；取样在分页之前，翻页不会改变基准。
        sample = (await session.scalars(select(current).order_by(*order).limit(30))).all()
        hit_summary, hit_ids = summarize_work_hits(sample)
    rows = (
        await session.execute(
            select(current, func.coalesce(User.display_name, User.username))
            .outerjoin(
                User,
                (User.id == current.created_by) & (User.tenant_id == tenant_id),
            )
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for row, name in rows:
        item = work_item(row, name)
        item.is_hit = row.id in hit_ids
        items.append(item)
    return WorkPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        hit_summary=hit_summary,
    )
