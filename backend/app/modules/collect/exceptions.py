"""U13 采集模块异常（继承 core/exceptions 基类）。"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    PermissionDeniedError,
    ResourceNotFoundError,
)


class WorkerTokenInvalid(AppException):
    code = "WORKER_TOKEN_INVALID"
    status_code = 401
    message = "Worker token 无效"


class WorkerIpForbidden(PermissionDeniedError):
    code = "WORKER_IP_FORBIDDEN"
    message = "Worker IP 不在白名单内"


class CredTokenInvalid(PermissionDeniedError):
    code = "CRED_TOKEN_INVALID"
    message = "凭据交换令牌无效、过期或已使用"


class CrawlerTaskNotFound(ResourceNotFoundError):
    code = "CRAWLER_TASK_NOT_FOUND"
    message = "采集任务不存在"


class DqIssueNotFound(ResourceNotFoundError):
    code = "DQ_ISSUE_NOT_FOUND"
    message = "数据质量异常记录不存在"


__all__ = [
    "CrawlerTaskNotFound",
    "CredTokenInvalid",
    "DqIssueNotFound",
    "WorkerIpForbidden",
    "WorkerTokenInvalid",
]
