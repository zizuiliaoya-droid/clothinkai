"""平台发起只读排查；不是客户登录或可续期的会话。"""

from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.modules.auth.deps import BypassSessionDep, CurrentActiveUser
from app.modules.auth.platform_access import require_platform_admin
from app.modules.auth.support_access import issue_support_token

router = APIRouter(prefix="/api/platform", tags=["platform-support"])


class SupportSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID


@router.post("/support-sessions")
async def start_support_session(
    payload: SupportSessionRequest,
    response: Response,
    actor: CurrentActiveUser,
    session: BypassSessionDep,
) -> dict:
    await require_platform_admin(session, actor.id)
    response.headers["Cache-Control"] = "no-store"
    return await issue_support_token(session, actor, payload.tenant_id)
