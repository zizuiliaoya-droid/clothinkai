"""U07 催发模板编排（EP08-S04）。"""

from __future__ import annotations

from uuid import UUID

from app.modules.wecom.domain import validate_template_vars
from app.modules.wecom.exceptions import WecomTemplateInvalidVarError
from app.modules.wecom.models import MessageTemplate
from app.modules.wecom.repository import MessageTemplateRepository

# 默认模板（seed / 首次访问回退）
_DEFAULTS: dict[str, str] = {
    "urge": "{博主昵称}您好，{商品简称}的发布日期（{预定发布日期}）快到了，"
    "还剩{剩余天数}天，请尽快安排发布~",
    "urge_important": "{博主昵称}您好，{商品简称}的发布日期（{预定发布日期}）"
    "即将到期（剩余{剩余天数}天），请务必尽快发布！",
}


class MessageTemplateService:
    def __init__(self, session) -> None:
        self._s = session
        self._repo = MessageTemplateRepository(session)

    async def upsert(
        self, template_type: str, content: str, actor_id: UUID | None
    ) -> MessageTemplate:
        invalid = validate_template_vars(content)
        if invalid:
            raise WecomTemplateInvalidVarError(invalid)
        tpl = await self._repo.get(template_type)
        if tpl is None:
            tpl = MessageTemplate(
                template_type=template_type, content=content, updated_by=actor_id
            )
            self._repo.add(tpl)
        else:
            tpl.content = content
            tpl.updated_by = actor_id
        await self._s.flush()
        return tpl

    async def get(self, template_type: str) -> MessageTemplate | None:
        return await self._repo.get(template_type)

    async def seed_defaults(self) -> None:
        """幂等 seed 两类默认模板（lifespan 调用，仅当缺失时）。"""
        existing = await self._repo.get_all()
        for ttype, content in _DEFAULTS.items():
            if ttype not in existing:
                self._repo.add(
                    MessageTemplate(template_type=ttype, content=content)
                )
        await self._s.flush()

    async def load_rendered_map(self) -> dict[str, str]:
        """返回 {template_type: content}，缺失用默认。"""
        existing = await self._repo.get_all()
        return {
            t: (existing[t].content if t in existing else _DEFAULTS[t])
            for t in ("urge", "urge_important")
        }


__all__ = ["MessageTemplateService"]
