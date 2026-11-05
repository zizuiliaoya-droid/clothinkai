"""作品快照 API，沿用博主权限及平台 support 只读限制。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.db import get_session
from app.core.exceptions import ResourceNotFoundError
from app.modules.auth.deps import CurrentActiveUser, CurrentPerms, require_permission
from app.modules.blogger.work_models import BloggerWorkSnapshot
from app.modules.blogger.work_schemas import WorkInput, WorkPage, WorkSaveResult
from app.modules.blogger.work_service import list_works, require_blogger, save_work
from app.modules.importer import access
from app.modules.importer.service import ImportService

router = APIRouter(prefix="/bloggers", dependencies=[require_permission("blogger", "read")])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{blogger_id}/works", response_model=WorkPage)
async def works(
    blogger_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    date_from: date | None = None,
    date_to: date | None = None,
    snapshot_id: UUID | None = None,
) -> WorkPage:
    result = await list_works(
        session,
        user.tenant_id,
        blogger_id,
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        snapshot_id=snapshot_id,
    )
    result.can_write = perms.has("blogger", "write")
    result.can_read_source = access.can_view(perms, "manual_blogger_work")
    return result


@router.post(
    "/{blogger_id}/works",
    response_model=WorkSaveResult,
    dependencies=[require_permission("blogger", "write")],
)
async def add_work(
    blogger_id: UUID,
    payload: WorkInput,
    user: CurrentActiveUser,
    session: Session,
) -> WorkSaveResult:
    row, inserted = await save_work(session, user.tenant_id, blogger_id, payload, user.id)
    result = WorkSaveResult(id=row.id, inserted=inserted)
    await session.commit()
    return result


@router.get("/{blogger_id}/works/{snapshot_id}/source")
async def source_file(
    blogger_id: UUID,
    snapshot_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: Session,
) -> dict[str, str]:
    await require_blogger(session, user.tenant_id, blogger_id)
    row = await session.scalar(
        select(BloggerWorkSnapshot).where(
            BloggerWorkSnapshot.id == snapshot_id,
            BloggerWorkSnapshot.blogger_id == blogger_id,
            BloggerWorkSnapshot.tenant_id == user.tenant_id,
        )
    )
    if row is None or row.import_batch_id is None:
        raise ResourceNotFoundError("该记录没有导入原文件")
    batch = await ImportService(session).get_batch(row.import_batch_id, user, perms)
    return {"url": attachment_service.get_signed_url("private", batch.file_r2_key)}
