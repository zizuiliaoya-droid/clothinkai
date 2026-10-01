"""催发任务枚举。"""

from __future__ import annotations

from enum import Enum


class UrgeTaskStatus(str, Enum):
    """催发任务状态。

    只有两态，刻意不做复杂状态机：催发这件事要么还在催，要么不用催了。
    「不用催了」的三种原因记在 ``close_reason`` 里，不拆成三个状态 ——
    看板只关心进行中还是关闭了。
    """

    OPEN = "进行中"
    CLOSED = "已关闭"


class UrgeCloseReason(str, Enum):
    """任务关闭原因。

    前两个由系统写：博主发文或单据取消时自动关闭（PRD「博主确认发布 → 任务自动关闭」）。
    ``MANUAL`` 留给主管手动收口，例如已经决定走召回流程、不再催了。
    """

    PUBLISHED = "博主已发布"
    CANCELLED = "已取消"
    MANUAL = "手动关闭"


class UrgeTriggerType(str, Enum):
    """这次催发是谁发起的。

    看板要分开统计（自动催了多少次、人工介入多少次），所以落库而不是靠
    ``created_by IS NULL`` 推断 —— 自动任务也可能带上发起人。
    """

    MANUAL = "手动"
    AUTO = "自动"


__all__ = ["UrgeCloseReason", "UrgeTaskStatus", "UrgeTriggerType"]
