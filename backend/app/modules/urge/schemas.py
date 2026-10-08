"""催发任务 Pydantic Schema。"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.urge.enums import UrgeTaskStatus, UrgeTriggerType

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class UrgeConfigUpdate(BaseModel):
    """修改催发阈值。

    默认值与 DB ``server_default`` 保持一致（照 ``AlertConfigUpdate`` 的做法：
    默认值放两处，DB 兜底、Pydantic 兜住 API）。区间也在两处校验 —— CHECK 约束挡
    绕过 API 的直写，Field 挡住明显的误填并给出友好报错。
    """

    no_publish_days: int = Field(default=5, ge=1, le=60)
    max_urge_times: int = Field(default=3, ge=1, le=20)
    max_overdue_days: int = Field(default=30, ge=1, le=365)
    urge_threshold_days: int = Field(default=10, ge=1, le=60)
    important_threshold_days: int = Field(default=3, ge=0, le=60)
    auto_scan_enabled: bool = True

    @model_validator(mode="after")
    def _important_not_above_urge(self) -> UrgeConfigUpdate:
        if self.important_threshold_days > self.urge_threshold_days:
            raise ValueError("「重要催发」阈值不能大于「催发」阈值，否则重要催发永远取不到")
        return self


class UrgeConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    no_publish_days: int
    max_urge_times: int
    max_overdue_days: int
    urge_threshold_days: int
    important_threshold_days: int
    auto_scan_enabled: bool


# ---------------------------------------------------------------------------
# 任务与留痕
# ---------------------------------------------------------------------------


class UrgeRecordResponse(BaseModel):
    """一条催发留痕。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trigger_type: str
    note: str | None = None
    screenshot_url: str | None = None
    """截图的签名 URL，现签不落库（与收款码、款式主图同一套做法）。"""

    wecom_message_id: UUID | None = None
    created_by: UUID | None = None
    created_by_name: str | None = None
    created_at: datetime


class UrgeTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    promotion_id: UUID
    promotion_internal_code: str | None = None
    blogger_id: UUID
    blogger_nickname: str | None = None
    pr_id: UUID | None = None
    pr_name: str | None = None

    style_code: str | None = None
    style_name: str | None = None
    """建单时的款式简称快照（接口兼容保留）。界面显示 ``display_short_name``。"""
    display_short_name: str | None = None
    """品名：归属商品的简称，没填回落快照（7a-8，规则见 ``promotion/display_name.py``）。"""
    goods_title: str | None = None
    """归属商品全称（悬停提示用）。没有归属商品为 None。"""
    scheduled_publish_date: date | None = None
    publish_status: str | None = None
    """推广单当前的发布状态。任务关闭后仍保留，方便核对关闭原因对不对。"""

    status: str
    urge_count: int
    last_urged_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None

    over_limit: bool = False
    """已催次数是否超过 ``max_urge_times``。

    服务端算而不是让前端拿配置自己比：阈值可配，前端各处比一遍迟早有地方忘了改。
    """

    overdue_days: int | None = None
    """超期天数（今天 - 预定发布日）。未排期或还没到期为 None。"""

    created_at: datetime
    updated_at: datetime


class UrgeTaskDetailResponse(UrgeTaskResponse):
    """任务详情 = 任务 + 完整时间线（倒序）。"""

    records: list[UrgeRecordResponse] = Field(default_factory=list)


class UrgeTaskListResponse(BaseModel):
    items: list[UrgeTaskResponse]
    total: int
    page: int = 1
    page_size: int = 20


class UrgeTaskListFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: UrgeTaskStatus | None = None
    pr_id: UUID | None = None
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    over_limit_only: bool = False
    """只看催过头的（催发次数 > 阈值）—— 主管据此决定要不要召回。"""

    overdue_only: bool = False
    """只看已过预定发布日的。"""

    keyword: str | None = Field(default=None, max_length=64)
    """搜博主昵称、推广单编码、款号。"""


# ---------------------------------------------------------------------------
# 发起催发
# ---------------------------------------------------------------------------


class UrgeCreateRequest(BaseModel):
    """手动催发单条（不带截图时用 JSON；带截图走 multipart 端点）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=2000)


class UrgeBatchRequest(BaseModel):
    """按款式批量催发。

    PRD「手动随时发起（单条 + 按款式批量）」。批量不支持截图 —— 一次催十几个博主
    贴同一张截图没有留痕价值，要截图就逐条催。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    style_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class UrgeBatchResponse(BaseModel):
    style_id: UUID
    urged_count: int
    """实际催发的单据数。"""

    task_ids: list[UUID]
    skipped_closed: int = 0
    """跳过的已关闭任务数（博主已发布 / 单据已取消）。"""


class UrgeCloseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=2000)
    """关闭备注，写进留痕时间线。关闭原因固定为「手动关闭」。"""


# ---------------------------------------------------------------------------
# 主管看板
# ---------------------------------------------------------------------------


class UrgeDashboardResponse(BaseModel):
    """PRD「主管看板：本周已催发 N / 待催发 M / 超时未回 K」。"""

    urged_this_week: int
    """本周（周一至今，租户时区）产生的催发留痕条数。按次数算而不是按任务数 ——
    同一单催三次是三次工作量。"""

    pending: int
    """进行中的任务数。"""

    overdue: int
    """进行中且已过预定发布日的任务数。"""

    over_limit: int
    """进行中且催发次数超过阈值的任务数 —— 这批该考虑召回或转取消。"""

    auto_scan_enabled: bool
    max_urge_times: int
    week_start: date
    """本周起始日，让前端能把「本周」显示成具体区间而不是含糊的字眼。"""


# ---------------------------------------------------------------------------
# 扫描结果（给 Celery 任务回报，不走 HTTP）
# ---------------------------------------------------------------------------


class UrgeScanResult(BaseModel):
    tasks_created: int = 0
    records_created: int = 0
    skipped_same_day: int = 0
    """当天已自动催过，跳过。"""

    skipped_too_overdue: int = 0
    """超期超过 ``max_overdue_days``，不自动催（可手动）。"""

    tasks_closed: int = 0
    """扫描时顺手关掉的任务（单据已发布 / 已取消但任务还开着）。"""

    trigger_type: UrgeTriggerType = UrgeTriggerType.AUTO


__all__ = [
    "UrgeBatchRequest",
    "UrgeBatchResponse",
    "UrgeCloseRequest",
    "UrgeConfigResponse",
    "UrgeConfigUpdate",
    "UrgeCreateRequest",
    "UrgeDashboardResponse",
    "UrgeRecordResponse",
    "UrgeScanResult",
    "UrgeTaskDetailResponse",
    "UrgeTaskListFilters",
    "UrgeTaskListResponse",
    "UrgeTaskResponse",
]
