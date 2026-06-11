"""U03 blogger 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.blogger.service import BloggerService


def get_blogger_service(session: SessionDep) -> BloggerService:
    return BloggerService(session)


BloggerServiceDep = Annotated[BloggerService, Depends(get_blogger_service)]


__all__ = ["BloggerServiceDep", "get_blogger_service"]
