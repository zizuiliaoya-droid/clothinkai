"""U07 wecom 模块 FastAPI 依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.wecom.alert_config_service import AlertConfigService
from app.modules.wecom.bind_service import WecomBindService
from app.modules.wecom.config_service import WecomConfigService
from app.modules.wecom.notification_service import NotificationService
from app.modules.wecom.repository import WecomMessageRepository
from app.modules.wecom.template_service import MessageTemplateService


def get_config_service(session: SessionDep) -> WecomConfigService:
    return WecomConfigService(session)


def get_bind_service(session: SessionDep) -> WecomBindService:
    return WecomBindService(session)


def get_template_service(session: SessionDep) -> MessageTemplateService:
    return MessageTemplateService(session)


def get_notification_service(session: SessionDep) -> NotificationService:
    return NotificationService(session)


def get_message_repo(session: SessionDep) -> WecomMessageRepository:
    return WecomMessageRepository(session)


def get_alert_config_service(session: SessionDep) -> AlertConfigService:
    return AlertConfigService(session)


ConfigServiceDep = Annotated[WecomConfigService, Depends(get_config_service)]
BindServiceDep = Annotated[WecomBindService, Depends(get_bind_service)]
TemplateServiceDep = Annotated[MessageTemplateService, Depends(get_template_service)]
NotificationServiceDep = Annotated[
    NotificationService, Depends(get_notification_service)
]
MessageRepoDep = Annotated[WecomMessageRepository, Depends(get_message_repo)]
AlertConfigServiceDep = Annotated[
    AlertConfigService, Depends(get_alert_config_service)
]


__all__ = [
    "AlertConfigServiceDep",
    "BindServiceDep",
    "ConfigServiceDep",
    "MessageRepoDep",
    "NotificationServiceDep",
    "TemplateServiceDep",
    "get_alert_config_service",
    "get_bind_service",
    "get_config_service",
    "get_message_repo",
    "get_notification_service",
    "get_template_service",
]
