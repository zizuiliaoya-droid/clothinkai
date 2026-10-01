"""催发任务业务异常。"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    ResourceNotFoundError,
    ValidationError,
)


class UrgeTaskNotFoundError(ResourceNotFoundError):
    code = "URGE_TASK_NOT_FOUND"


class UrgeTaskClosedError(AppException):
    """已关闭的任务不能再催。

    博主已经发了、或者单据已取消，再催就是打扰人。要继续催得先确认单据状态。
    """

    code = "URGE_TASK_CLOSED"
    status_code = 409


class UrgeNotApplicableError(AppException):
    """这个单据不该有催发任务。

    催发的前提是「还没发布」。已发布 / 已取消 / 已删除的单据建催发任务没有意义，
    而且会把看板的「待催发」数字搞脏。
    """

    code = "URGE_NOT_APPLICABLE"
    status_code = 409


class UrgeScreenshotInvalidError(ValidationError):
    """催发截图不合格（格式 / 大小 / 内容与声明不符）。"""

    code = "URGE_SCREENSHOT_INVALID"


class UrgeConfigInvalidError(ValidationError):
    """阈值配置不合法。"""

    code = "URGE_CONFIG_INVALID"


class UrgeBatchEmptyError(ValidationError):
    """按款式批量催发时没有命中任何可催的单据。

    报错而不是静默返回 0：PR 点了批量催发却什么都没发生，得知道是为什么
    （款式下的单都已发布，还是压根没有未发布的单）。
    """

    code = "URGE_BATCH_EMPTY"


__all__ = [
    "UrgeBatchEmptyError",
    "UrgeConfigInvalidError",
    "UrgeNotApplicableError",
    "UrgeScreenshotInvalidError",
    "UrgeTaskClosedError",
    "UrgeTaskNotFoundError",
]
