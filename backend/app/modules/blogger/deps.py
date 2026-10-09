"""U03 blogger 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_dict import BloggerTagDictService


def get_blogger_service(session: SessionDep) -> BloggerService:
    return BloggerService(session)


BloggerServiceDep = Annotated[BloggerService, Depends(get_blogger_service)]


def get_blogger_tag_dict_service(session: SessionDep) -> BloggerTagDictService:
    return BloggerTagDictService(session)


BloggerTagDictServiceDep = Annotated[BloggerTagDictService, Depends(get_blogger_tag_dict_service)]


__all__ = [
    "BloggerServiceDep",
    "BloggerTagDictServiceDep",
    "get_blogger_service",
    "get_blogger_tag_dict_service",
]
