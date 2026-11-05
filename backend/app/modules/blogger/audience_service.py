"""按维度原子保存画像，旧日期仅归档，不覆盖其他维度或历史键。"""

from copy import deepcopy
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.blogger.audience_models import BloggerAudienceCapture as Capture
from app.modules.blogger.audience_schemas import (
    AudienceInput,
    AudienceItem,
    AudiencePage,
    AudienceSaveResult,
    current_dimensions,
)
from app.modules.blogger.models import Blogger
from app.modules.importer.models import ImportBatch


async def get_blogger(
    session: AsyncSession, tenant_id: UUID, blogger_id: UUID, *, lock: bool = False
) -> Blogger:
    query = select(Blogger).where(
        Blogger.tenant_id == tenant_id, Blogger.id == blogger_id, Blogger.is_deleted.is_(False)
    )
    row = await session.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if row is None:
        raise ResourceNotFoundError("博主不存在")
    return row


async def save_audience(
    session: AsyncSession,
    tenant_id: UUID,
    blogger_id: UUID,
    payload: AudienceInput,
    actor_id: UUID | None,
    batch_id: UUID | None = None,
    row_numbers: list[int] | None = None,
) -> AudienceSaveResult:
    blogger = await get_blogger(session, tenant_id, blogger_id, lock=True)
    if (
        batch_id
        and await session.scalar(
            select(ImportBatch.id).where(
                ImportBatch.tenant_id == tenant_id, ImportBatch.id == batch_id
            )
        )
        is None
    ):
        raise ResourceNotFoundError("导入批次不存在")
    row = await session.scalar(
        select(Capture).where(
            Capture.tenant_id == tenant_id,
            Capture.blogger_id == blogger_id,
            Capture.dimension == payload.dimension,
            Capture.collected_on == payload.collected_on,
        )
    )
    data = payload.model_dump(mode="json")
    previous = current_dimensions(blogger.audience_profile).get(payload.dimension)
    if row is not None:
        if AudienceInput.model_validate(row, from_attributes=True).model_dump(mode="json") != data:
            raise ValidationError(
                "同一维度同一采集日期已有不同内容，请核对采集日期和分布，不会覆盖原记录"
            )
        current = previous is not None and previous["record_id"] == str(row.id)
        return AudienceSaveResult(
            id=row.id, inserted=False, current=current, message="已有相同采集记录，未重复添加"
        )
    row = Capture(
        **payload.model_dump(exclude={"entries"}),
        entries=data["entries"],
        tenant_id=tenant_id,
        blogger_id=blogger_id,
        created_by=actor_id,
        import_batch_id=batch_id,
        row_numbers=row_numbers or [],
    )
    session.add(row)
    await session.flush()
    current = previous is None or data["collected_on"] > previous["collected_on"]
    if current:
        profile = deepcopy(blogger.audience_profile or {})
        dimensions = dict(current_dimensions(profile))
        dimensions[payload.dimension] = {**data, "record_id": str(row.id)}
        profile["structured_v1"] = {"dimensions": dimensions}
        blogger.audience_profile = profile
        await session.flush()
    return AudienceSaveResult(
        id=row.id,
        inserted=True,
        current=current,
        message="画像已更新" if current else "采集日期较旧，已归档，当前画像保持不变",
    )


async def list_audience(
    session: AsyncSession,
    tenant_id: UUID,
    blogger_id: UUID,
    *,
    history: bool = False,
    page: int = 1,
    page_size: int = 10,
) -> AudiencePage:
    blogger = await get_blogger(session, tenant_id, blogger_id)
    ids = [
        UUID(value["record_id"]) for value in current_dimensions(blogger.audience_profile).values()
    ]
    query = select(Capture).where(Capture.tenant_id == tenant_id, Capture.blogger_id == blogger_id)
    if not history:
        query = query.where(Capture.id.in_(ids))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.execute(
            query.add_columns(func.coalesce(User.display_name, User.username))
            .outerjoin(User, (User.id == Capture.created_by) & (User.tenant_id == tenant_id))
            .order_by(Capture.collected_on.desc(), Capture.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for row, name in rows:
        item = AudienceItem.model_validate(row)
        item.created_by_name = name
        item.is_current = row.id in ids
        items.append(item)
    return AudiencePage(items=items, total=total, page=page, page_size=page_size)
