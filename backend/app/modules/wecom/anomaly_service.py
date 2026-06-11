"""U15 S10 异常预警（投产聚合 → 阈值判定 → 去重 → 自建应用推送）。

复用 U14 ProductionService（last_7d）；阈值实时读 wecom_alert_config（即时生效）。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.exc import IntegrityError

from app.core.metrics import wecom_anomaly_alert_total
from app.core.security.crypto import decrypt_credential
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.domain import resolve_time_range
from app.modules.report.production_service import ProductionService
from app.modules.wecom.alert_models import WecomAlertLog
from app.modules.wecom.client import WecomClient, build_http_client
from app.modules.wecom.enums import AlertType
from app.modules.wecom.exceptions import WecomApiError, WecomRateLimited
from app.modules.wecom.repository import (
    WecomAlertConfigRepository,
    WecomAlertLogRepository,
    WecomConfigRepository,
)

log = logging.getLogger(__name__)

_WINDOW = "last_7d"

_TITLES = {
    AlertType.RETURN_RATE_HIGH.value: "退货退款率过高",
    AlertType.ROI_LOW.value: "净投产比过低",
}
_ADVICE = {
    AlertType.RETURN_RATE_HIGH.value: "建议核查款式质量 / 详情页 / 物流时效",
    AlertType.ROI_LOW.value: "建议优化投放或暂停低效推广",
}


class AnomalyAlertService:
    def __init__(self, session) -> None:
        self._s = session
        self._alert_cfg = WecomAlertConfigRepository(session)
        self._log_repo = WecomAlertLogRepository(session)
        self._wecom_cfg = WecomConfigRepository(session)

    async def check_and_alert(self, tenant_id: UUID) -> int:
        cfg = await self._alert_cfg.get()
        if cfg is None or not cfg.is_enabled:
            return 0
        tr = resolve_time_range(_WINDOW, None, None)
        report = await ProductionService(self._s).get_report(tenant_id, tr)
        sent = 0
        for row in report.items:
            for alert_type, detail in self._evaluate_row(row, cfg):
                sent += await self._fire(tenant_id, alert_type, row, detail, cfg)
        return sent

    @staticmethod
    def _evaluate_row(row: Any, cfg: Any) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        if (
            row.return_rate is not None
            and row.return_rate > cfg.return_rate_threshold
        ):
            out.append((
                AlertType.RETURN_RATE_HIGH.value,
                {"value": str(row.return_rate),
                 "threshold": str(cfg.return_rate_threshold)},
            ))
        if (
            cfg.low_roi_threshold is not None
            and row.net_roi is not None
            and row.net_roi < cfg.low_roi_threshold
        ):
            out.append((
                AlertType.ROI_LOW.value,
                {"value": str(row.net_roi),
                 "threshold": str(cfg.low_roi_threshold)},
            ))
        # conversion_low：V1 口径缺失占位，不检（BR-U15-23）
        return out

    async def _fire(
        self, tenant_id: UUID, alert_type: str, row: Any, detail: dict, cfg: Any
    ) -> int:
        period_key = get_today().isoformat()
        entity_ref = str(row.style_id)
        if await self._log_repo.exists(
            alert_type=alert_type, entity_ref=entity_ref, period_key=period_key
        ):
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="deduped"
            ).inc()
            return 0
        if not cfg.alert_recipients:
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="no_recipient"
            ).inc()
            return 0  # 不落 log（配置补齐后可补推）

        markdown = self._render(alert_type, row, detail)
        wecom_cfg = await self._wecom_cfg.get()
        if wecom_cfg is None or not wecom_cfg.is_active:
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="failed"
            ).inc()
            return 0

        def _secret() -> str:
            return decrypt_credential(
                tenant_id, wecom_cfg.id, wecom_cfg.secret_ciphertext,
                purpose="wecom_alert",
            )

        async def _secret_provider() -> str:
            return _secret()

        http = build_http_client()
        try:
            client = WecomClient(
                tenant_id, wecom_cfg, http=http, secret_provider=_secret_provider
            )
            await client.send_app_message(list(cfg.alert_recipients), markdown)
            self._log_repo.add(WecomAlertLog(
                alert_type=alert_type, entity_type="style", entity_ref=entity_ref,
                period_key=period_key,
                detail={**detail, "style_code": row.style_code},
            ))
            await self._s.flush()  # 成功才落 log（IntegrityError 并发 → deduped）
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="sent"
            ).inc()
            return 1
        except IntegrityError:
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="deduped"
            ).inc()
            return 0
        except (WecomApiError, WecomRateLimited, httpx.HTTPError) as exc:
            log.warning(
                "anomaly_alert_send_failed",
                extra={"tenant_id": str(tenant_id), "err": str(exc)},
            )
            wecom_anomaly_alert_total.labels(
                alert_type=alert_type, status="failed"
            ).inc()
            return 0
        finally:
            await http.aclose()

    @staticmethod
    def _render(alert_type: str, row: Any, detail: dict) -> str:
        return (
            f"**异常预警·{_TITLES.get(alert_type, alert_type)}**\n"
            f"> 款号：{row.style_code} {row.style_name}\n"
            f"> 当前值：{detail['value']}（阈值：{detail['threshold']}）\n"
            f"> 建议：{_ADVICE.get(alert_type, '请关注')}"
        )


__all__ = ["AnomalyAlertService"]
