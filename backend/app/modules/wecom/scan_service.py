"""U07 催发扫描编排（EP08-S05，由 Celery Beat 任务逐租户调用）。

按 P-U07-03：候选筛选（复用 U04 find_urge_candidates）→ 按 (blogger,pr) 聚合 →
幂等跳过 → 未绑定 notify → 建 pending message。返回新建 message id 列表（任务 commit 后 delay）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from app.modules.promotion.repository import PromotionRepository
from app.modules.wecom.domain import build_render_ctx, is_important, render_template
from app.modules.wecom.enums import NotificationType
from app.modules.wecom.models import WecomMessage
from app.modules.wecom.notification_service import NotificationService
from app.modules.wecom.repository import (
    WecomContactRepository,
    WecomMessageRepository,
)
from app.modules.wecom.template_service import MessageTemplateService

_URGE_DAYS = 10
_IMPORTANT_DAYS = 3


class WecomScanService:
    def __init__(self, session) -> None:
        self._s = session
        self._messages = WecomMessageRepository(session)
        self._contacts = WecomContactRepository(session)
        self._notify = NotificationService(session)
        self._templates = MessageTemplateService(session)

    async def scan_tenant(self, today: date) -> list[UUID]:
        promos = await PromotionRepository(self._s).find_urge_candidates(
            today=today, urge_days=_URGE_DAYS, important_days=_IMPORTANT_DAYS
        )
        groups: dict[tuple, list] = defaultdict(list)
        for row in promos:
            groups[(row["blogger_id"], row["pr_id"])].append(row)

        template_map = await self._templates.load_rendered_map()
        created: list[UUID] = []

        for (blogger_id, pr_id), items in groups.items():
            if await self._messages.exists_today_non_failed(
                blogger_id=blogger_id, pr_id=pr_id, today=today
            ):
                continue  # 扫描幂等（BR-U07-34）

            contact = await self._contacts.get_by_blogger(blogger_id)
            if contact is None:
                if pr_id is not None:
                    await self._notify.notify(
                        [pr_id],
                        f"博主 {items[0]['blogger_nickname']} 未绑定企微，无法自动催发",
                        type=NotificationType.URGE_UNBOUND.value,
                    )
                continue  # BR-U07-33

            important = any(
                is_important(
                    scheduled_publish_date=it["scheduled_publish_date"],
                    today=today,
                    publish_status=it["publish_status"],
                    urge_days=_URGE_DAYS,
                    important_days=_IMPORTANT_DAYS,
                )
                for it in items
            )
            tt = "urge_important" if important else "urge"
            ctx = build_render_ctx(
                blogger_nickname=items[0]["blogger_nickname"],
                style_short_name=items[0]["style_short_name_snapshot"],
                scheduled_publish_date=items[0]["scheduled_publish_date"],
                today=today,
            )
            content = render_template(template_map[tt], ctx)

            msg = WecomMessage(
                blogger_id=blogger_id,
                pr_id=pr_id,
                external_userid=contact.external_userid,
                template_type=tt,
                rendered_content=content,
                promotion_ids=[str(it["promotion_id"]) for it in items],
                status="pending",
            )
            self._messages.add(msg)
            await self._s.flush()
            created.append(msg.id)

        return created


__all__ = ["WecomScanService"]
