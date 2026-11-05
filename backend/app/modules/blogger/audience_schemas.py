"""局部画像：百分数有明确单位，年龄保留来源标签。"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Dimension = Literal["province", "city_tier", "age", "gender"]
DIMENSION_LABELS = {
    "province": "地域省份",
    "city_tier": "城市级别",
    "age": "年龄",
    "gender": "性别",
}
CITY_TIERS = frozenset(
    {"一线", "新一线", "二线", "三线", "四线", "五线", "六线及以下", "特区", "其他"}
)


def parse_percentage(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("占比须为百分数")
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("占比请填写23.5或23.5%，均表示23.5%") from exc
    if not parsed.is_finite() or not 0 <= parsed <= 100:
        raise ValueError("占比须在0%～100%之间")
    return float(parsed)


class AudienceEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    label: str = Field(min_length=1, max_length=64)
    percentage: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    tgi: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("percentage", mode="before")
    @classmethod
    def percentage_unit(cls, value: Any) -> float | None:
        return parse_percentage(value)

    @field_validator("tgi", mode="before")
    @classmethod
    def blank_tgi(cls, value: Any) -> Any:
        return None if value is None or isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def require_value(self) -> "AudienceEntry":
        if self.percentage is None and self.tgi is None:
            raise ValueError("分类须填写占比或有来源的TGI")
        return self


class AudienceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    dimension: Dimension
    collected_on: date
    source: str = Field(min_length=1, max_length=128)
    completeness: Literal["complete", "partial"]
    estimated: bool = False
    tgi_source: str | None = Field(default=None, max_length=256)
    entries: list[AudienceEntry] = Field(min_length=1, max_length=100)

    @field_validator("tgi_source", mode="before")
    @classmethod
    def empty_source(cls, value: Any) -> Any:
        return None if value is None or isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def distribution(self) -> "AudienceInput":
        labels = [entry.label for entry in self.entries]
        if len(set(labels)) != len(labels):
            raise ValueError("同一分布不能重复填写分类")
        if self.dimension == "city_tier" and not set(labels) <= CITY_TIERS:
            raise ValueError("城市级别须使用一线/新一线等层级；省份请选地域省份维度")
        if self.dimension == "province" and set(labels) & CITY_TIERS - {"其他"}:
            raise ValueError("城市层级不能填入地域省份")
        if any(e.tgi is not None for e in self.entries) and not self.tgi_source:
            raise ValueError("填写TGI时须注明实际来源和口径")
        if self.completeness == "complete":
            if any(e.percentage is None for e in self.entries):
                raise ValueError("完整分布的每个分类都须填写占比")
            total = sum(Decimal(str(e.percentage)) for e in self.entries)
            if not Decimal("99") <= total <= Decimal("101"):
                raise ValueError("完整分布占比合计须在99%～101%之间；缺项请选择部分数据")
        self.entries.sort(key=lambda entry: entry.label)
        return self


class AudienceItem(AudienceInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    import_batch_id: UUID | None
    row_numbers: list[int]
    created_by_name: str | None = None
    is_current: bool = False


class AudiencePage(BaseModel):
    items: list[AudienceItem]
    total: int
    page: int = 1
    page_size: int = 10
    can_write: bool = False
    can_read_source: bool = False


class AudienceSaveResult(BaseModel):
    id: UUID
    inserted: bool
    current: bool
    message: str


def current_dimensions(profile: dict | None) -> dict[str, Any]:
    value = (profile or {}).get("structured_v1")
    return cast(dict[str, Any], value.get("dimensions", {})) if isinstance(value, dict) else {}


def audience_updated_on(profile: dict | None) -> date | None:
    values = [
        date.fromisoformat(item["collected_on"]) for item in current_dimensions(profile).values()
    ]
    return max(values) if values else None
