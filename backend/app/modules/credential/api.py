"""U12 平台凭据 API（/api/credentials）。

7 端点；响应永不含明文密码（CredentialPublic）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.credential.deps import CredentialServiceDep
from app.modules.credential.schemas import (
    CredentialCreate,
    CredentialPage,
    CredentialPublic,
    CredentialUpdate,
)

router = APIRouter(prefix="/api/credentials", tags=["credential"])


@router.post(
    "/",
    response_model=CredentialPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("credential", "write")],
)
async def create_credential(
    payload: CredentialCreate,
    user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> CredentialPublic:
    """EP07-S02 管理员添加平台凭据（加密存储，默认 paused）。"""
    return await service.create(payload, user)


@router.get(
    "/",
    response_model=CredentialPage,
    dependencies=[require_permission("credential", "read")],
)
async def list_credentials(
    user: CurrentActiveUser,
    service: CredentialServiceDep,
    platform: Annotated[str | None, Query(max_length=16)] = None,
    cred_status: Annotated[str | None, Query(max_length=16)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CredentialPage:
    """EP07-S03 凭据列表（不含密码）。"""
    return await service.list(
        tenant_id=user.tenant_id,
        platform=platform,
        status=cred_status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{credential_id}",
    response_model=CredentialPublic,
    dependencies=[require_permission("credential", "read")],
)
async def get_credential(
    credential_id: UUID,
    _user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> CredentialPublic:
    """EP07-S03 凭据详情（不含密码）。"""
    return await service.get(credential_id)


@router.put(
    "/{credential_id}",
    response_model=CredentialPublic,
    dependencies=[require_permission("credential", "write")],
)
async def update_credential(
    credential_id: UUID,
    payload: CredentialUpdate,
    user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> CredentialPublic:
    return await service.update(credential_id, payload, user)


@router.put(
    "/{credential_id}/pause",
    response_model=CredentialPublic,
    dependencies=[require_permission("credential", "write")],
)
async def pause_credential(
    credential_id: UUID,
    user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> CredentialPublic:
    """EP07-S05 暂停凭据。"""
    return await service.pause(credential_id, user)


@router.put(
    "/{credential_id}/resume",
    response_model=CredentialPublic,
    dependencies=[require_permission("credential", "write")],
)
async def resume_credential(
    credential_id: UUID,
    user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> CredentialPublic:
    """EP07-S05 恢复凭据（重置失败计数）。"""
    return await service.resume(credential_id, user)


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[require_permission("credential", "delete")],
)
async def delete_credential(
    credential_id: UUID,
    user: CurrentActiveUser,
    service: CredentialServiceDep,
) -> dict[str, bool]:
    """EP07-S05 删除凭据（硬删，密文清除）。"""
    await service.delete(credential_id, user)
    return {"ok": True}


__all__ = ["router"]
