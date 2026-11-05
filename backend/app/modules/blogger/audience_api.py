"""结构化画像与采集记录，沿用博主及导入原文件权限。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.db import get_session
from app.core.exceptions import ResourceNotFoundError
from app.modules.auth.deps import CurrentActiveUser, CurrentPerms, require_permission
from app.modules.blogger.audience_models import BloggerAudienceCapture
from app.modules.blogger.audience_schemas import AudienceInput, AudiencePage, AudienceSaveResult
from app.modules.blogger.audience_service import get_blogger, list_audience, save_audience
from app.modules.importer import access
from app.modules.importer.service import ImportService

router = APIRouter(prefix="/bloggers", dependencies=[require_permission("blogger", "read")])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{blogger_id}/audience", response_model=AudiencePage)
async def audience(
    blogger_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: Session,
    history: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> AudiencePage:
    result = await list_audience(
        session, user.tenant_id, blogger_id, history=history, page=page, page_size=page_size
    )
    result.can_write = perms.has("blogger", "write")
    result.can_read_source = access.can_view(perms, "manual_blogger_audience")
    return result


@router.post(
    "/{blogger_id}/audience",
    response_model=AudienceSaveResult,
    dependencies=[require_permission("blogger", "write")],
)
async def add_audience(
    blogger_id: UUID, payload: AudienceInput, user: CurrentActiveUser, session: Session
) -> AudienceSaveResult:
    result = await save_audience(session, user.tenant_id, blogger_id, payload, user.id)
    await session.commit()
    return result


@router.get("/{blogger_id}/audience/{capture_id}/source")
async def source_file(
    blogger_id: UUID,
    capture_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: Session,
) -> dict[str, str]:
    await get_blogger(session, user.tenant_id, blogger_id)
    row = await session.scalar(
        select(BloggerAudienceCapture).where(
            BloggerAudienceCapture.id == capture_id,
            BloggerAudienceCapture.blogger_id == blogger_id,
            BloggerAudienceCapture.tenant_id == user.tenant_id,
        )
    )
    if row is None or row.import_batch_id is None:
        raise ResourceNotFoundError("该记录没有导入原文件")
    batch = await ImportService(session).get_batch(row.import_batch_id, user, perms)
    return {"url": attachment_service.get_signed_url("private", batch.file_r2_key)}
