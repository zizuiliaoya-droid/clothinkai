"""U15 企微预警配置 Schema（写入校验 + 读响应脱敏）。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class AlertConfigUpdate(BaseModel):
    """预警配置写入（PUT /api/wecom/alert-config）。"""

    control_group_webhook: str | None = None
    return_rate_threshold: Decimal = Field(Decimal("0.4000"), ge=0, le=1)
    low_roi_threshold: Decimal | None = Field(None, gt=0)
    low_conversion_threshold: Decimal | None = Field(None, ge=0, le=1)
    alert_recipients: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class AlertConfigResponse(BaseModel):
    """预警配置读响应（webhook 脱敏，不回显完整 URL）。"""

    webhook_configured: bool
    webhook_mask: str | None = None
    return_rate_threshold: Decimal
    low_roi_threshold: Decimal | None = None
    low_conversion_threshold: Decimal | None = None
    alert_recipients: list[str]
    is_enabled: bool


__all__ = ["AlertConfigResponse", "AlertConfigUpdate"]
