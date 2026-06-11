"""U10a design 模块 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


class DesignCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    style_code: str = Field(..., min_length=1, max_length=64)
    style_name: str = Field(..., min_length=1, max_length=255)
    main_image_key: str | None = Field(default=None, max_length=512)
    category: str = Field(default="连衣裙", max_length=32)
    short_name: str | None = Field(default=None, max_length=64)


class FabricSubmit(BaseModel):
    fabrics: list[dict] = Field(..., min_length=1)
    accessories: list[dict] = Field(default_factory=list)
    remark: str | None = None


class FabricComplete(BaseModel):
    fabrics: list[dict] | None = None
    accessories: list[dict] | None = None
    remark: str | None = None


class PatternSubmit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    pattern_no: str = Field(..., min_length=1, max_length=64)
    pattern_file_key: str | None = Field(default=None, max_length=512)


class GradingSubmit(BaseModel):
    grading_data: dict = Field(...)


class CraftSubmit(BaseModel):
    craft_info: dict = Field(...)


class CostBreakdown(BaseModel):
    fabric_cost: Decimal = Field(..., ge=0)
    accessory_cost: Decimal = Field(..., ge=0)
    craft_cost: Decimal = Field(..., ge=0)


class CostingSubmit(BaseModel):
    cost_breakdown: CostBreakdown


class TagPriceSubmit(BaseModel):
    tag_price: Decimal = Field(..., gt=0)


class RejectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=512)


class CancelRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class WorkflowLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None
    to_status: str
    action: str
    driven_by: str | None
    reason: str | None
    created_at: datetime


class DesignDetailResponse(BaseModel):
    id: UUID
    style_code: str
    style_name: str
    design_status: str
    main_image_key: str | None = None
    fabric: dict | None = None
    pattern: dict | None = None
    craft: dict | None = None
    workflow_log: list[WorkflowLogEntry] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)


class DesignListItem(BaseModel):
    id: UUID
    style_code: str
    style_name: str
    design_status: str
    main_image_key: str | None = None


class DesignStatusGroup(BaseModel):
    status: str
    count: int
    items: list[DesignListItem] = Field(default_factory=list)


class DesignListResponse(BaseModel):
    groups: list[DesignStatusGroup] = Field(default_factory=list)
    total: int


__all__ = [
    "CancelRequest",
    "CostBreakdown",
    "CostingSubmit",
    "CraftSubmit",
    "DesignCreate",
    "DesignDetailResponse",
    "DesignListItem",
    "DesignListResponse",
    "DesignStatusGroup",
    "FabricComplete",
    "FabricSubmit",
    "GradingSubmit",
    "PatternSubmit",
    "RejectRequest",
    "TagPriceSubmit",
    "WorkflowLogEntry",
]
