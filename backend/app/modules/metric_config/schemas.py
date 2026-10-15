"""平台指标配置接口的 Schema（``GET`` / ``PUT /api/settings/promotion-platforms``，报表视图设计 5.1）。

PUT 收全量 5 行：平台只认 ``Platform`` 的 5 个，每个恰好一次；系数 ``0 < x ≤ 10``、最多 3 位小数，
阈值整数 1 ~ 10,000,000（与表上的 CHECK 同范围）。违者 422 ``VALIDATION_ERROR``，``loc`` 带行号
（``["body", 2, "like_coefficient"]``），前端按行号把错误设到单元格。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, RootModel, model_validator
from pydantic_core import PydanticCustomError

from app.modules.blogger.enums import Platform

_COEF_MIN_EXCLUSIVE = Decimal("0")
_COEF_MAX = Decimal("10")


def _coef_in_range(value: Decimal) -> Decimal:
    # 不用 Field(gt=, le=)：Decimal 字段的边界进 422 的 ctx 时是 Decimal，全局处理器直接
    # JSONResponse 序列化不了，变成 500。自己判，错误里不带 Decimal。
    if not (_COEF_MIN_EXCLUSIVE < value <= _COEF_MAX):
        raise PydanticCustomError("like_coefficient_range", "折算系数须大于 0 且不超过 10")
    return value


LikeCoefficient = Annotated[
    Decimal, Field(allow_inf_nan=False, decimal_places=3), AfterValidator(_coef_in_range)
]
HitThreshold = Annotated[int, Field(ge=1, le=10_000_000)]

_COEF_QUANTUM = Decimal("0.001")


class PlatformSettingIn(BaseModel):
    platform: Platform
    like_coefficient: LikeCoefficient
    hit_threshold: HitThreshold


class PlatformSettingsUpdate(RootModel[list[PlatformSettingIn]]):
    """PUT 的 body 是裸列表。"""

    @model_validator(mode="after")
    def _each_platform_once(self) -> PlatformSettingsUpdate:
        # PydanticCustomError 而不是 ValueError：后者把异常对象放进 ctx，422 序列化时变 500
        seen = [row.platform for row in self.root]
        dup = sorted({p.value for p in seen if seen.count(p) > 1})
        if dup:
            raise PydanticCustomError(
                "platform_duplicated", "平台重复：{platforms}", {"platforms": "、".join(dup)}
            )
        missing = [p.value for p in Platform if p not in seen]
        if missing:
            raise PydanticCustomError(
                "platform_missing", "缺少平台：{platforms}", {"platforms": "、".join(missing)}
            )
        return self


class PlatformSettingOut(BaseModel):
    platform: str
    like_coefficient: Decimal
    hit_threshold: int

    @classmethod
    def of(cls, platform: str, coef: Decimal, threshold: int) -> PlatformSettingOut:
        # 统一 3 位小数（= numeric(6,3)）：默认值 Decimal("0.1") 与表里读出的 0.100 在页面上一个样
        return cls(
            platform=platform,
            like_coefficient=coef.quantize(_COEF_QUANTUM),
            hit_threshold=threshold,
        )


class PlatformSettingsResponse(BaseModel):
    items: list[PlatformSettingOut]
    """5 个平台，按枚举顺序；没写过行的平台是默认值。"""
    can_edit: bool
    """当前用户有没有 ``metric_config:write``（前端据此切只读）。"""
    changed: list[str] = Field(default_factory=list)
    """本次 PUT 实际改了的平台；GET 与无变化的 PUT 为空。"""
    quality_recompute_queued: bool = False
    """本次 PUT 是否已投递博主质量标签重算。"""


__all__ = [
    "PlatformSettingIn",
    "PlatformSettingOut",
    "PlatformSettingsResponse",
    "PlatformSettingsUpdate",
]
