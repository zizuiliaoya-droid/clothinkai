"""页面筛选记忆 API（PRD 第 7 章 user_filter_pref）。

复用 U17 ``user_preference`` 表，不另开表。读写的都是调用者自己的偏好，
所以只要求登录，不叠加业务权限 —— 换句话说这里没有跨用户读写的入口。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path

from app.modules.auth.deps import CurrentActiveUser
from app.modules.report.deps import UserPreferenceServiceDep
from app.modules.report.user_preference_service import (
    sanitize_filter_payload,
    validate_filter_payload,
)

router = APIRouter(prefix="/api/preferences", tags=["preference"])

_PageCode = Annotated[str, Path(max_length=64, description="页面编码，如 product_roi")]


@router.get("/filters/{page_code}")
async def get_filter_preference(
    page_code: _PageCode,
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
) -> dict:
    """取回该页面上次保存的筛选条件；没存过返回空字典。"""
    validate_filter_payload(page_code, {})
    # 存量记录里可能还有已下线的筛选项（如 product_roi 的 category），读出时剔掉
    return sanitize_filter_payload(page_code, await service.get_filter(user.id, page_code))


@router.put("/filters/{page_code}")
async def save_filter_preference(
    page_code: _PageCode,
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
    filters: Annotated[dict, Body(...)],
) -> dict:
    validate_filter_payload(page_code, filters)
    # 保存前剔掉已下线的筛选项，库里不再新增
    await service.save_filter(user, page_code, sanitize_filter_payload(page_code, filters))
    return {"ok": True}


__all__ = ["router"]
