"""U15 S09 发文通知控评（群机器人 best-effort）。

由 Celery 任务 notify_control_group 调用；重读 promotion 校验状态防回滚误发。
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from app.core.metrics import wecom_group_notify_total
from app.modules.blogger.repository import BloggerRepository
from app.modules.promotion.repository import PromotionRepository
from app.modules.wecom.client import WecomClient, build_http_client
from app.modules.wecom.exceptions import WecomApiError, WecomRateLimited
from app.modules.wecom.repository import WecomAlertConfigRepository

log = logging.getLogger(__name__)


class GroupNotifyService:
    def __init__(self, session) -> None:
        self._s = session
        self._promos = PromotionRepository(session)
        self._bloggers = BloggerRepository(session)
        self._alert_cfg = WecomAlertConfigRepository(session)

    async def notify_publish(self, promotion_id: UUID, tenant_id: UUID) -> dict:
        promo = await self._promos.get_by_id(promotion_id)
        # 防回滚误发：publish 事务可能已回滚 / 状态被改（BR-U15-03）
        if promo is None or promo.publish_status != "已发布" or not promo.publish_url:
            return {"status": "skipped"}

        cfg = await self._alert_cfg.get()
        if cfg is None or not cfg.control_group_webhook:
            log.warning(
                "control_group_webhook_unconfigured",
                extra={"tenant_id": str(tenant_id)},
            )
            wecom_group_notify_total.labels(status="unconfigured").inc()
            return {"status": "unconfigured"}

        blogger = await self._bloggers.get_by_id(promo.blogger_id)
        nickname = blogger.nickname if blogger else str(promo.blogger_id)
        markdown = (
            "**新笔记发布·待控评**\n"
            f"> 款号：{promo.style_code_snapshot}（{promo.internal_code}）\n"
            f"> 博主：{nickname}\n"
            f"> 链接：[{promo.publish_url}]({promo.publish_url})\n"
            f"> 日期：{promo.actual_publish_date}"
        )
        http = build_http_client()
        try:
            client = WecomClient(
                tenant_id, None, http=http, secret_provider=None
            )
            await client.send_group_robot(cfg.control_group_webhook, markdown)
            wecom_group_notify_total.labels(status="sent").inc()
            return {"status": "sent"}
        except (WecomApiError, WecomRateLimited, httpx.HTTPError) as exc:
            log.warning(
                "group_notify_failed",
                extra={"tenant_id": str(tenant_id), "err": str(exc)},
            )
            wecom_group_notify_total.labels(status="failed").inc()
            return {"status": "failed"}
        finally:
            await http.aclose()


__all__ = ["GroupNotifyService"]
