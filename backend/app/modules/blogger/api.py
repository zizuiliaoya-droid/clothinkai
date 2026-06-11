"""U03 blogger 模块 REST API 路由。

按 business-logic-model.md 6 个 UC 实现 8 个端点。
全部端点：
- 应用 ``require_permission("blogger:read|write|delete")``
- 通过 deps 注入 service
- 抛出业务异常 → 全局 error handler 自动映射

降级语义：
- 业务未匹配 → 200 + 空数组（service 层处理）
- 系统失败 → 异常自然冒泡 → 5xx + Sentry
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.modules.auth.deps import (
    CurrentActiveUser,
    require_permission,
)
from app.modules.blogger.deps import BloggerServiceDep
from app.modules.blogger.repository import BloggerListFilters
from app.modules.blogger.schemas import (
    BloggerCreate,
    BloggerPage,
    BloggerResponse,
    BloggerUpdate,
)

router = APIRouter(prefix="/api", tags=["blogger"])


# ---------------------------------------------------------------------------
# Blogger CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/bloggers/",
    response_model=BloggerResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("blogger", "write")],
)
async def create_blogger(
    payload: BloggerCreate,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> BloggerResponse:
    """EP04-S01 PR 添加博主."""
    return await service.create_blogger(payload, user)


@router.get(
    "/bloggers/",
    response_model=BloggerPage,
    dependencies=[require_permission("blogger", "read")],
)
async def list_bloggers(
    user: CurrentActiveUser,
    service: BloggerServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=128)] = None,
    blogger_type: Annotated[str | None, Query(max_length=16)] = None,
    follower_count_min: Annotated[int | None, Query(ge=0)] = None,
    follower_count_max: Annotated[int | None, Query(ge=0)] = None,
    category_tag: Annotated[str | None, Query(max_length=32)] = None,
    quality_tag: Annotated[str | None, Query(max_length=32)] = None,
    platform: Annotated[str | None, Query(max_length=16)] = None,
    is_suspected_fake: bool | None = None,
    is_active: bool = True,
    include_inactive: bool = False,
) -> BloggerPage:
    """EP04-S03 博主搜索筛选 + 列表（合并接口）.

    关键参数：
    - keyword：ILIKE 模糊匹配 nickname / xiaohongshu_id（wechat 仅在用户具有 CONTACT_VISIBLE_ROLES 时参与匹配，防侧信道）
    - category_tag / quality_tag：JSONB 包含查询
    - follower_count_min/max：粉丝量范围
    """
    filters = BloggerListFilters(
        keyword=keyword,
        blogger_type=blogger_type,
        follower_count_min=follower_count_min,
        follower_count_max=follower_count_max,
        category_tag=category_tag,
        quality_tag=quality_tag,
        platform=platform,
        is_suspected_fake=is_suspected_fake,
        is_active=is_active,
        include_inactive=include_inactive,
    )
    return await service.list_bloggers(
        filters=filters, page=page, page_size=page_size, user=user
    )


@router.get(
    "/bloggers/{blogger_id}",
    response_model=BloggerResponse,
    dependencies=[require_permission("blogger", "read")],
)
async def get_blogger(
    blogger_id: UUID,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> BloggerResponse:
    return await service.get_blogger(blogger_id, user)


@router.put(
    "/bloggers/{blogger_id}",
    response_model=BloggerResponse,
    dependencies=[require_permission("blogger", "write")],
)
async def update_blogger(
    blogger_id: UUID,
    payload: BloggerUpdate,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> BloggerResponse:
    """EP04-S02 PR 编辑博主信息（含 quote audit 脱敏）."""
    return await service.update_blogger(blogger_id, payload, user)


@router.delete(
    "/bloggers/{blogger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("blogger", "delete")],
)
async def delete_blogger(
    blogger_id: UUID,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> Response:
    await service.soft_delete_blogger(blogger_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bloggers/{blogger_id}/disable",
    response_model=BloggerResponse,
    dependencies=[require_permission("blogger", "write")],
)
async def disable_blogger(
    blogger_id: UUID,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> BloggerResponse:
    return await service.disable_blogger(blogger_id, user)


@router.post(
    "/bloggers/{blogger_id}/restore",
    response_model=BloggerResponse,
    dependencies=[require_permission("blogger", "delete")],
)
async def restore_blogger(
    blogger_id: UUID,
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> BloggerResponse:
    """BR-U03-21 恢复软删的博主（管理员）."""
    return await service.restore_blogger(blogger_id, user)


@router.post(
    "/bloggers/recompute-tags",
    dependencies=[require_permission("blogger.tag", "recompute")],
)
async def recompute_blogger_tags(
    user: CurrentActiveUser,
    service: BloggerServiceDep,
) -> dict[str, int]:
    """EP04-S05 管理员触发：重算当前租户全部活跃博主智能标签.

    同步重算（小数据量）；大数据量由 Celery ``recompute_all_blogger_tags`` 定时跑。
    """
    return await service.recompute_tags_for_current_tenant(user.tenant_id)


__all__ = ["router"]
