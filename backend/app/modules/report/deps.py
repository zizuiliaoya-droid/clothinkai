"""U08 report 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.report.bi_service import BiService
from app.modules.report.export_service import ReportExportService
from app.modules.report.production_service import ProductionService
from app.modules.report.service import PublishProgressService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.target_planning_service import TargetPlanningService
from app.modules.report.user_preference_service import UserPreferenceService
from app.modules.report.work_progress_service import WorkProgressService


def get_publish_progress_service(session: SessionDep) -> PublishProgressService:
    return PublishProgressService(session)


PublishProgressServiceDep = Annotated[
    PublishProgressService, Depends(get_publish_progress_service)
]


def get_work_progress_service(session: SessionDep) -> WorkProgressService:
    return WorkProgressService(session)


WorkProgressServiceDep = Annotated[
    WorkProgressService, Depends(get_work_progress_service)
]


def get_target_planning_service(session: SessionDep) -> TargetPlanningService:
    return TargetPlanningService(session)


TargetPlanningServiceDep = Annotated[
    TargetPlanningService, Depends(get_target_planning_service)
]


def get_store_daily_service(session: SessionDep) -> StoreDailyService:
    return StoreDailyService(session)


StoreDailyServiceDep = Annotated[
    StoreDailyService, Depends(get_store_daily_service)
]


def get_production_service(session: SessionDep) -> ProductionService:
    return ProductionService(session)


ProductionServiceDep = Annotated[
    ProductionService, Depends(get_production_service)
]


def get_bi_service(session: SessionDep) -> BiService:
    return BiService(session)


BiServiceDep = Annotated[BiService, Depends(get_bi_service)]


def get_export_service(session: SessionDep) -> ReportExportService:
    return ReportExportService(session)


ExportServiceDep = Annotated[
    ReportExportService, Depends(get_export_service)
]


def get_user_preference_service(session: SessionDep) -> UserPreferenceService:
    return UserPreferenceService(session)


UserPreferenceServiceDep = Annotated[
    UserPreferenceService, Depends(get_user_preference_service)
]


__all__ = [
    "BiServiceDep",
    "ExportServiceDep",
    "ProductionServiceDep",
    "PublishProgressServiceDep",
    "StoreDailyServiceDep",
    "TargetPlanningServiceDep",
    "UserPreferenceServiceDep",
    "WorkProgressServiceDep",
    "get_bi_service",
    "get_export_service",
    "get_production_service",
    "get_publish_progress_service",
    "get_store_daily_service",
    "get_target_planning_service",
    "get_user_preference_service",
    "get_work_progress_service",
]
