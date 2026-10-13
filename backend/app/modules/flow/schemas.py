"""时间线读接口的响应形状（流程线设计 6.5）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TimelineActor(BaseModel):
    id: UUID
    display_name: str


class TimelineAttachment(BaseModel):
    id: UUID
    url: str | None
    """私有桶签名（900 秒）；签名失败为 null（附件还在，只是这次拿不到地址）。"""
    mime: str


class TimelineEvent(BaseModel):
    seq: int
    event_type: str
    group: str
    """分组中文名（谈款 / 留言 / 推广 / 发货 / 结款 / 复盘），节点图标按它区分。"""
    created_at: datetime
    actor: TimelineActor | None
    """null = 系统（扫描、导入）。"""
    from_state: str | None
    to_state: str | None
    note: str | None
    attachment: TimelineAttachment | None
    payload: dict[str, Any]
    """按 ``PAYLOAD_FIELD_RULES`` 裁剪后（没登记的键、读不到的字段都删掉）。"""


class TimelineNegotiation(BaseModel):
    id: UUID
    status: str


class TimelinePromotion(BaseModel):
    id: UUID
    internal_code: str
    stage: str


class TimelineNextStep(BaseModel):
    text: str
    who: str


class HiddenSegment(BaseModel):
    group: str
    count: int
    reason: str


class LegacyPoint(BaseModel):
    at: datetime
    text: str
    source: str


class LineTimelineResponse(BaseModel):
    line_id: UUID
    negotiation: TimelineNegotiation | None
    """看不到谈款段时为 null。"""
    promotion: TimelinePromotion | None
    next_step: TimelineNextStep | None = None
    events: list[TimelineEvent]
    """最新在上（``seq`` 倒序）。"""
    hidden_segments: list[HiddenSegment] = Field(default_factory=list)
    legacy: list[LegacyPoint] = Field(default_factory=list)
    """上线前记录（按单据字段还原）。"""
    truncated: bool = False
    """超过 500 条只回最新的 500 条。"""
    ui: dict[str, Any] = Field(default_factory=lambda: {"actions": {}})
    """``ui.actions.comment``：留言框出不出现（与 ``POST /comments`` 同一份矩阵判定）。"""


__all__ = [
    "HiddenSegment",
    "LegacyPoint",
    "LineTimelineResponse",
    "TimelineActor",
    "TimelineAttachment",
    "TimelineEvent",
    "TimelineNegotiation",
    "TimelineNextStep",
    "TimelinePromotion",
]
