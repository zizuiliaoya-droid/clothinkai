"""U15 wecom 事件监听器（S09 发文通知控评）。

监听 U04 ``PromotionPublished``（通知类 required_handler=False）：
- 事务内仅入队 Celery 任务 notify_control_group（不做 HTTP / 不写库）
- HTTP 在任务内异步执行，且任务重读 promotion 校验状态防回滚误发（BR-U15-02/03）

注册位置：main.py register_event_listeners 末尾（通知类，缺失不阻塞）。
"""

from __future__ import annotations

import logging

from app.core.events import subscribe
from app.modules.promotion.events import PromotionPublished

log = logging.getLogger(__name__)


async def on_promotion_published(event: PromotionPublished, session) -> None:
    """S09：笔记发布 → 入队控评群通知（事务内不 HTTP）。"""
    from app.tasks.wecom_tasks import notify_control_group

    notify_control_group.delay(str(event.promotion_id), str(event.tenant_id))


def register() -> None:
    """U15 wecom listener 注册（通知类 PromotionPublished）。"""
    subscribe("PromotionPublished", on_promotion_published)


__all__ = ["on_promotion_published", "register"]
