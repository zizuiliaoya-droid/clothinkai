"""字典管理 API（dict_item）：类目/季节/颜色/尺码 可维护字典 CRUD。

端点：
- GET  /api/dict-items?dict_type=category  → 列表（含 is_active 筛选）
- POST /api/dict-items                     → 新增
- DELETE /api/dict-items/{item_id}          → 删除（硬删）
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.product.dict_models import DictItem

router = APIRouter(prefix="/api", tags=["dict"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class DictItemCreate(BaseModel):
    dict_type: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=64)
    sort_order: int = Field(default=0, ge=0)


class DictItemResponse(BaseModel):
    id: str
    dict_type: str
    value: str
    sort_order: int
    is_active: bool


@router.get(
    "/dict-items",
    response_model=list[DictItemResponse],
    dependencies=[require_permission("product", "read")],
)
async def list_dict_items(
    user: CurrentActiveUser,
    session: SessionDep,
    dict_type: Annotated[str | None, Query(max_length=32)] = None,
    is_active: bool = True,
) -> list[DictItemResponse]:
    stmt = (
        select(DictItem)
        .where(DictItem.tenant_id == user.tenant_id, DictItem.is_active == is_active)
    )
    if dict_type:
        stmt = stmt.where(DictItem.dict_type == dict_type)
    stmt = stmt.order_by(DictItem.sort_order.asc(), DictItem.value.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return [
        DictItemResponse(
            id=str(r.id), dict_type=r.dict_type, value=r.value,
            sort_order=r.sort_order, is_active=r.is_active,
        )
        for r in rows
    ]


@router.post(
    "/dict-items",
    response_model=DictItemResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("product", "write")],
)
async def create_dict_item(
    payload: DictItemCreate,
    user: CurrentActiveUser,
    session: SessionDep,
) -> DictItemResponse:
    stmt = (
        pg_insert(DictItem)
        .values(
            tenant_id=user.tenant_id,
            dict_type=payload.dict_type,
            value=payload.value,
            sort_order=payload.sort_order,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "dict_type", "value"])
        .returning(DictItem)
    )
    result = await session.execute(stmt)
    row = result.scalars().first()
    if row is None:
        # 冲突 → 已存在，直接查回
        existing = (
            await session.execute(
                select(DictItem).where(
                    DictItem.tenant_id == user.tenant_id,
                    DictItem.dict_type == payload.dict_type,
                    DictItem.value == payload.value,
                )
            )
        ).scalar_one()
        row = existing
    else:
        await session.commit()
    return DictItemResponse(
        id=str(row.id), dict_type=row.dict_type, value=row.value,
        sort_order=row.sort_order, is_active=row.is_active,
    )


@router.delete(
    "/dict-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("product", "write")],
)
async def delete_dict_item(
    item_id: UUID,
    user: CurrentActiveUser,
    session: SessionDep,
) -> Response:
    await session.execute(
        delete(DictItem).where(
            DictItem.id == item_id, DictItem.tenant_id == user.tenant_id
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
