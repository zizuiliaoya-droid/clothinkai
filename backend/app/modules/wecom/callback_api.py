"""U07 企微回调 REST API（公开端点，无 JWT，靠签名校验）。

按 P-U07-05：tenant 路由 /api/wecom/callback/{tenant_id}；GET 验证 URL（echostr 回显），
POST 接收消息回调（推进 wecom_message.status）。签名失败 403 + audit。

DB 访问：用 AsyncSessionApp + SET LOCAL app.tenant_id（无 JWT 驱动 SessionDep）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from app.core.db import AsyncSessionApp
from app.modules.wecom.callback_service import WecomCallbackService
from app.modules.wecom.repository import WecomConfigRepository

router = APIRouter(prefix="/api/wecom", tags=["wecom-callback"])


async def _load_config(tenant_id: UUID):
    """用 app 会话 + SET LOCAL 加载该租户配置（RLS 生效）。"""
    async with AsyncSessionApp() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        return await WecomConfigRepository(s).get()


@router.get("/callback/{tenant_id}", response_class=PlainTextResponse)
async def verify_callback_url(
    tenant_id: Annotated[UUID, Path()],
    msg_signature: Annotated[str, Query()],
    timestamp: Annotated[str, Query()],
    nonce: Annotated[str, Query()],
    echostr: Annotated[str, Query()],
) -> str:
    cfg = await _load_config(tenant_id)
    if cfg is None:
        # 不暴露租户存在性；统一签名失败语义
        from app.modules.wecom.exceptions import WecomCallbackBadSignatureError

        raise WecomCallbackBadSignatureError()
    async with AsyncSessionApp() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        plaintext = await WecomCallbackService(s).verify_url(
            cfg,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
        )
        await s.commit()
    return plaintext


@router.post("/callback/{tenant_id}", response_class=PlainTextResponse)
async def receive_callback(
    tenant_id: Annotated[UUID, Path()],
    msg_signature: Annotated[str, Query()],
    timestamp: Annotated[str, Query()],
    nonce: Annotated[str, Query()],
    encrypt: Annotated[str, Body(embed=True)],
) -> str:
    cfg = await _load_config(tenant_id)
    if cfg is None:
        from app.modules.wecom.exceptions import WecomCallbackBadSignatureError

        raise WecomCallbackBadSignatureError()
    async with AsyncSessionApp() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        await WecomCallbackService(s).handle(
            cfg,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypt=encrypt,
        )
        await s.commit()
    return "success"


__all__ = ["router"]
