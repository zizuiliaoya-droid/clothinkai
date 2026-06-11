"""U04 promotion 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.promotion.service import PromotionService


def get_promotion_service(session: SessionDep) -> PromotionService:
    return PromotionService(session)


PromotionServiceDep = Annotated[PromotionService, Depends(get_promotion_service)]


__all__ = ["PromotionServiceDep", "get_promotion_service"]
