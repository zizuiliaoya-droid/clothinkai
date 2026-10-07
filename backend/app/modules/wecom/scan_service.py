"""U07 催发扫描编排（EP08-S05，由 Celery Beat 任务逐租户调用）。

按 P-U07-03：候选筛选（复用 U04 find_urge_candidates）→ 按 (blogger,pr) 聚合 →
幂等跳过 → 未绑定 notify → 建 pending message。返回新建 message id 列表（任务 commit 后 delay）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.repository import PromotionRepository
from app.modules.urge.service import UrgeService
from app.modules.wecom.domain import build_render_ctx, is_important, render_template
from app.modules.wecom.enums import NotificationType
from app.modules.wecom.models import WecomMessage
from app.modules.wecom.notification_service import NotificationService
from app.modules.wecom.repository import (
    WecomContactRepository,
    WecomMessageRepository,
)
from app.modules.wecom.template_service import MessageTemplateService


class WecomScanService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._messages = WecomMessageRepository(session)
        self._contacts = WecomContactRepository(session)
        self._notify = NotificationService(session)
        self._templates = MessageTemplateService(session)

    async def scan_tenant(self, today: date, *, tenant_id: UUID | None = None) -> list[UUID]:
        """扫描并建企微催发消息。

        阈值从 ``urge_config`` 读（PRD 改动 2「阈值后台可配」）。原来这里有一份
        ``_URGE_DAYS = 10 / _IMPORTANT_DAYS = 3``，和
        ``promotion/legacy_settings.py`` 里的同名常量各写一遍 —— 双份真相，
        谁改一边就不一致。现在两边都读同一张配置表。

        ``tenant_id`` 可选只为兼容既有调用：取不到就回落默认阈值，行为与改造前一致。
        """
        cfg = (
            await UrgeService(self._s).get_effective_config(tenant_id)
            if tenant_id is not None
            else {"urge_threshold_days": 10, "important_threshold_days": 3}
        )
        urge_days = cfg["urge_threshold_days"]
        important_days = cfg["important_threshold_days"]

        promos = await PromotionRepository(self._s).find_urge_candidates(
            today=today, urge_days=urge_days, important_days=important_days
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
                    urge_days=urge_days,
                    important_days=important_days,
                )
                for it in items
            )
            tt = "urge_important" if important else "urge"
            ctx = build_render_ctx(
                blogger_nickname=items[0]["blogger_nickname"],
                # 模板变量「商品简称」= 商品简称，没填回落建单快照（7a-8，业务方 10-05）
                style_short_name=items[0]["display_short_name"],
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
