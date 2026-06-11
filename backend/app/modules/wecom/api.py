"""U07 wecom REST API（配置 / 绑定 / 模板 / 消息查询）。

端点（NF-5 权限）：
- PUT  /api/settings/wecom                 wecom.config:write   配置自建应用（secret 加密）
- GET  /api/settings/wecom                 wecom.config:write   读配置（不回显 secret）
- POST /api/settings/wecom/test            wecom.config:write   测试连接
- POST /api/bloggers/{id}/wecom-bind       wecom.bind:write     绑定外部联系人
- PUT  /api/settings/templates/{type}      wecom.template:write 编辑模板
- GET  /api/settings/templates/{type}      wecom.template:write 读模板
- GET  /api/wecom/messages                 wecom.message:read   消息记录列表
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.wecom.deps import (
    BindServiceDep,
    ConfigServiceDep,
    MessageRepoDep,
    TemplateServiceDep,
)
from app.modules.wecom.schemas import (
    TemplateResponse,
    TemplateUpdate,
    WecomBindResponse,
    WecomConfigResponse,
    WecomConfigUpdate,
    WecomMessageResponse,
    WecomTestResult,
)

router = APIRouter(prefix="/api", tags=["wecom"])

_VALID_TEMPLATE_TYPES = {"urge", "urge_important"}


# ---------------------------------------------------------------------------
# 配置（EP08-S02）
# ---------------------------------------------------------------------------


@router.put(
    "/settings/wecom",
    response_model=WecomConfigResponse,
    dependencies=[require_permission("wecom.config", "write")],
)
async def update_wecom_config(
    user: CurrentActiveUser,
    service: ConfigServiceDep,
    payload: WecomConfigUpdate,
) -> WecomConfigResponse:
    await service.configure(payload, user.tenant_id)
    resp = await service.get_response()
    assert resp is not None
    return resp


@router.get(
    "/settings/wecom",
    response_model=WecomConfigResponse,
    dependencies=[require_permission("wecom.config", "write")],
)
async def get_wecom_config(
    user: CurrentActiveUser, service: ConfigServiceDep
) -> WecomConfigResponse:
    resp = await service.get_response()
    if resp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "企微应用未配置")
    return resp


@router.post(
    "/settings/wecom/test",
    response_model=WecomTestResult,
    dependencies=[require_permission("wecom.config", "write")],
)
async def test_wecom_connection(
    user: CurrentActiveUser, service: ConfigServiceDep
) -> WecomTestResult:
    return await service.test_connection(user.tenant_id)


# ---------------------------------------------------------------------------
# 绑定（EP08-S03）
# ---------------------------------------------------------------------------


@router.post(
    "/bloggers/{blogger_id}/wecom-bind",
    response_model=WecomBindResponse,
    dependencies=[require_permission("wecom.bind", "write")],
)
async def bind_wecom_contact(
    user: CurrentActiveUser,
    service: BindServiceDep,
    blogger_id: Annotated[UUID, Path()],
) -> WecomBindResponse:
    contact = await service.bind_contact(blogger_id, user.tenant_id, user.id)
    return WecomBindResponse(
        blogger_id=contact.blogger_id,
        external_userid=contact.external_userid,
        bound_at=contact.bound_at,
    )


# ---------------------------------------------------------------------------
# 模板（EP08-S04）
# ---------------------------------------------------------------------------


@router.put(
    "/settings/templates/{template_type}",
    response_model=TemplateResponse,
    dependencies=[require_permission("wecom.template", "write")],
)
async def update_template(
    user: CurrentActiveUser,
    service: TemplateServiceDep,
    payload: TemplateUpdate,
    template_type: Annotated[str, Path()],
) -> TemplateResponse:
    if template_type not in _VALID_TEMPLATE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "未知模板类型")
    tpl = await service.upsert(template_type, payload.content, user.id)
    return TemplateResponse(template_type=tpl.template_type, content=tpl.content)


@router.get(
    "/settings/templates/{template_type}",
    response_model=TemplateResponse,
    dependencies=[require_permission("wecom.template", "write")],
)
async def get_template(
    user: CurrentActiveUser,
    service: TemplateServiceDep,
    template_type: Annotated[str, Path()],
) -> TemplateResponse:
    if template_type not in _VALID_TEMPLATE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "未知模板类型")
    tpl = await service.get(template_type)
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板未配置")
    return TemplateResponse(template_type=tpl.template_type, content=tpl.content)


# ---------------------------------------------------------------------------
# 消息记录（EP08-S06）
# ---------------------------------------------------------------------------


@router.get(
    "/wecom/messages",
    response_model=list[WecomMessageResponse],
    dependencies=[require_permission("wecom.message", "read")],
)
async def list_wecom_messages(
    user: CurrentActiveUser,
    repo: MessageRepoDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[WecomMessageResponse]:
    rows = await repo.list_recent(limit=limit, offset=offset)
    return [WecomMessageResponse.model_validate(r) for r in rows]


__all__ = ["router"]
