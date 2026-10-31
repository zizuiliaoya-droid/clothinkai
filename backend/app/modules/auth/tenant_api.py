"""平台租户管理；不使用业务权限通配符作为平台身份。"""

from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.modules.auth.deps import BypassSessionDep, CurrentActiveUser
from app.modules.auth.tenant_management import (
    TenantCreatedResponse,
    TenantListResponse,
    TenantManagementService,
    TenantStatusRequest,
    TenantView,
)
from app.modules.auth.tenant_onboarding import TenantOnboarding

router = APIRouter(prefix="/api/tenants", tags=["tenant-management"])


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    actor: CurrentActiveUser,
    session: BypassSessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> TenantListResponse:
    return await TenantManagementService(session).list(actor, page=page, page_size=page_size)


@router.post("", response_model=TenantCreatedResponse, status_code=201)
async def create_tenant(
    payload: TenantOnboarding,
    response: Response,
    actor: CurrentActiveUser,
    session: BypassSessionDep,
) -> TenantCreatedResponse:
    response.headers["Cache-Control"] = "no-store"
    return await TenantManagementService(session).create(actor, payload)


@router.put("/{tenant_id}/status", response_model=TenantView)
async def set_tenant_status(
    tenant_id: UUID,
    payload: TenantStatusRequest,
    actor: CurrentActiveUser,
    session: BypassSessionDep,
) -> TenantView:
    return await TenantManagementService(session).set_status(actor, tenant_id, payload.status)
