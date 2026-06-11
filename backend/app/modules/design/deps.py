"""U10a design 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.design.service import DesignService


def get_design_service(session: SessionDep) -> DesignService:
    return DesignService(session)


DesignServiceDep = Annotated[DesignService, Depends(get_design_service)]


__all__ = ["DesignServiceDep", "get_design_service"]
