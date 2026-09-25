"""U17 用户偏好服务（get_or_default / upsert + 页面筛选记忆）。

筛选记忆（PRD 第 7 章 ``user_filter_pref``）直接复用这张 ``user_preference`` 表：
``pref_key`` 承担 PRD 的 ``page_code``、``pref_value`` 承担 ``filter_json``，
不另开一张表。为了和 BI 布局这类非筛选偏好区分，筛选类 key 统一加 ``filter:`` 前缀。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.modules.report.user_preference_models import UserPreference

FILTER_PAGE_CODES: frozenset[str] = frozenset(
    {
        "product_roi",  # 投产报表
        "pr_work_progress",  # 工作进度表
        "store_daily",  # 店铺数据
        "bi_dashboard",  # BI 看板
    }
)
"""允许记忆筛选的页面编码白名单。

走白名单而不是任意 key：``pref_value`` 是 JSONB，开放写入等于给了一张任人塞数据的表。
"""

MAX_PREF_BYTES = 8 * 1024
"""单条偏好的 JSON 上限。筛选条件本该很小，超过这个量级说明前端塞错了东西。"""

_FILTER_KEY_PREFIX = "filter:"


def _filter_key(page_code: str) -> str:
    return f"{_FILTER_KEY_PREFIX}{page_code}"


def validate_filter_payload(page_code: str, filters: dict) -> None:
    """校验页面编码在白名单内、且载荷体积可控。"""
    if page_code not in FILTER_PAGE_CODES:
        raise ValidationError(
            f"不支持的页面编码：{page_code}",
            details={"allowed": sorted(FILTER_PAGE_CODES)},
        )
    size = len(json.dumps(filters, ensure_ascii=False).encode())
    if size > MAX_PREF_BYTES:
        raise ValidationError(
            "筛选条件过大，无法保存",
            details={"size_bytes": size, "limit_bytes": MAX_PREF_BYTES},
        )


class UserPreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_default(self, user_id: UUID, key: str, default: dict) -> dict:
        stmt = select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.pref_key == key,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return row.pref_value if row is not None else default

    async def upsert(self, user: Any, key: str, value: dict) -> None:
        stmt = (
            pg_insert(UserPreference)
            .values(
                tenant_id=user.tenant_id,
                user_id=user.id,
                pref_key=key,
                pref_value=value,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "pref_key"],
                set_={"pref_value": value, "updated_at": func.now()},
            )
        )
        await self._s.execute(stmt)
        await self._s.commit()

    async def get_filter(self, user_id: UUID, page_code: str) -> dict:
        """读取页面筛选偏好；没存过返回空字典（前端按自身默认值渲染）。"""
        return await self.get_or_default(user_id, _filter_key(page_code), {})

    async def save_filter(self, user: Any, page_code: str, filters: dict) -> None:
        """保存页面筛选偏好。

        整块 JSON 覆盖而不是合并：筛选条件是一个整体，用户清掉某一项就该消失，
        合并语义会让被清掉的条件复活。
        """
        await self.upsert(user, _filter_key(page_code), filters)


__all__ = [
    "FILTER_PAGE_CODES",
    "MAX_PREF_BYTES",
    "UserPreferenceService",
    "validate_filter_payload",
]
