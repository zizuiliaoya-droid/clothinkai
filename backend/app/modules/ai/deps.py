"""U18 AI 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.ai.service import AiAdvisoryService
from app.modules.auth.deps import SessionDep


def get_ai_advisory_service(session: SessionDep) -> AiAdvisoryService:
    return AiAdvisoryService(session)


AiAdvisoryServiceDep = Annotated[
    AiAdvisoryService, Depends(get_ai_advisory_service)
]


__all__ = ["AiAdvisoryServiceDep", "get_ai_advisory_service"]
