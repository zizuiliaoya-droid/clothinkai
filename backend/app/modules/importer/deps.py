"""U06a importer 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.importer.field_mapping_service import FieldMappingService
from app.modules.importer.service import ImportService


def get_import_service(session: SessionDep) -> ImportService:
    return ImportService(session)


def get_field_mapping_service(session: SessionDep) -> FieldMappingService:
    return FieldMappingService(session)


ImportServiceDep = Annotated[ImportService, Depends(get_import_service)]
FieldMappingServiceDep = Annotated[
    FieldMappingService, Depends(get_field_mapping_service)
]


__all__ = [
    "FieldMappingServiceDep",
    "ImportServiceDep",
    "get_field_mapping_service",
    "get_import_service",
]
