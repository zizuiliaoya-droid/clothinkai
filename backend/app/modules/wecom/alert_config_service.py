"""U15 企微预警配置服务（get 脱敏 + upsert 校验 + 审计）。"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.auth.models import User
from app.modules.wecom.alert_models import WecomAlertConfig
from app.modules.wecom.alert_schemas import AlertConfigResponse, AlertConfigUpdate
from app.modules.wecom.exceptions import AlertConfigInvalidError
from app.modules.wecom.repository import WecomAlertConfigRepository


class AlertConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = WecomAlertConfigRepository(session)
        self._audit = AuditService(session)

    async def get_response(self) -> AlertConfigResponse | None:
        cfg = await self._repo.get()
        if cfg is None:
            return None
        wh = cfg.control_group_webhook
        return AlertConfigResponse(
            webhook_configured=bool(wh),
            webhook_mask=("***" + wh[-6:]) if wh else None,
            return_rate_threshold=cfg.return_rate_threshold,
            low_roi_threshold=cfg.low_roi_threshold,
            low_conversion_threshold=cfg.low_conversion_threshold,
            alert_recipients=list(cfg.alert_recipients or []),
            is_enabled=cfg.is_enabled,
        )

    async def upsert(
        self, payload: AlertConfigUpdate, user: User
    ) -> WecomAlertConfig:
        if (
            payload.control_group_webhook
            and not payload.control_group_webhook.startswith("https://")
        ):
            raise AlertConfigInvalidError("webhook 须为 https URL")
        recipients = list(dict.fromkeys(payload.alert_recipients))  # 去重保序
        stmt = (
            pg_insert(WecomAlertConfig)
            .values(
                tenant_id=user.tenant_id,
                control_group_webhook=payload.control_group_webhook,
                return_rate_threshold=payload.return_rate_threshold,
                low_roi_threshold=payload.low_roi_threshold,
                low_conversion_threshold=payload.low_conversion_threshold,
                alert_recipients=recipients,
                is_enabled=payload.is_enabled,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id"],
                set_={
                    "control_group_webhook": payload.control_group_webhook,
                    "return_rate_threshold": payload.return_rate_threshold,
                    "low_roi_threshold": payload.low_roi_threshold,
                    "low_conversion_threshold": payload.low_conversion_threshold,
                    "alert_recipients": recipients,
                    "is_enabled": payload.is_enabled,
                    "updated_at": func.now(),
                },
            )
            .returning(WecomAlertConfig)
        )
        row = (await self._s.execute(stmt)).scalar_one()
        await self._audit.log(
            "wecom.alert_config.update",
            resource="wecom_alert_config",
            resource_id=row.id,
            user_id=user.id,
        )
        await self._s.commit()
        return row


__all__ = ["AlertConfigService"]
