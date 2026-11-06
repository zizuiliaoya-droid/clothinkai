"""作品输入与展示契约。计数可使用平台缩写，空值不是零。"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.importer.adapters.blogger import to_int

COUNTS = ("like_count", "comment_count", "share_count", "collect_count", "play_count")


def normalize_work_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("作品链接须为完整 http/https 地址")
    if any(c.isspace() for c in value):
        raise ValueError("作品链接不能含空白")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


class WorkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    work_id: str | None = Field(default=None, max_length=128)
    work_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    published_on: date
    collected_at: datetime
    source: str = Field(min_length=1, max_length=128)
    like_count: int | None = Field(default=None, ge=0, lt=2**63)
    comment_count: int | None = Field(default=None, ge=0, lt=2**63)
    share_count: int | None = Field(default=None, ge=0, lt=2**63)
    collect_count: int | None = Field(default=None, ge=0, lt=2**63)
    play_count: int | None = Field(default=None, ge=0, lt=2**63)

    @field_validator("work_id", "work_url", "title", mode="before")
    @classmethod
    def blank_to_none(cls, value: Any) -> Any:
        return None if value is None or str(value).strip() == "" else str(value).strip()

    @field_validator(*COUNTS, mode="before")
    @classmethod
    def parse_count(cls, value: Any) -> Any:
        result = to_int(value)
        if isinstance(result, str):
            raise ValueError("请填写非负整数，可使用 14.3w / 万 / 亿")
        return result

    @field_validator("collected_at", mode="before")
    @classmethod
    def require_time(cls, value: Any) -> Any:
        if not isinstance(value, datetime) and (
            not isinstance(value, str) or len(value.strip()) < 16
        ):
            raise ValueError("采集时间须含时分，例如 2026-11-05 14:30:00（北京时间）")
        return value

    @field_validator("collected_at")
    @classmethod
    def local_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone(timedelta(hours=8))) if value.tzinfo is None else value

    @field_validator("work_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return normalize_work_url(value)

    @model_validator(mode="after")
    def require_identity(self) -> "WorkInput":
        if not self.work_id and not self.work_url:
            raise ValueError("作品 ID 或稳定作品链接至少填写一项")
        return self

    @property
    def work_key(self) -> str:
        kind, value = ("id", self.work_id) if self.work_id else ("url", self.work_url)
        return kind + ":" + hashlib.sha256(str(value).encode()).hexdigest()


class WorkItem(WorkInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    platform: str
    import_batch_id: UUID | None
    row_number: int | None
    created_by: UUID | None
    created_by_name: str | None = None
    interaction_total: int | None = None
    collect_like_ratio: float | None = None
    share_like_ratio: float | None = None
    comment_like_ratio: float | None = None


class WorkPage(BaseModel):
    items: list[WorkItem]
    total: int
    page: int
    page_size: int
    can_write: bool = False
    can_read_source: bool = False


class WorkSaveResult(BaseModel):
    id: UUID
    inserted: bool
