"""U13 灰豚博主画像导入适配器（HuitunImportAdapter）。

source=huitun → 更新 blogger.audience_profile（U11 read_like_ratio 据此衍生）。
- 按 xiaohongshu_id 匹配 blogger
- 未匹配 → DataQualityIssue(warning)，不阻塞
- audience_profile JSON：{note_stats:{avg_likes,avg_reads}, gender, age, region ...}
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.models import Blogger
from app.modules.collect.data_quality_service import DataQualityService
from app.modules.importer.registry import ImportAdapterRegistry

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

_DEFAULT_COLUMNS = [
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "平均点赞", "target_field": "avg_likes", "type": "int"},
    {"source_col": "平均阅读", "target_field": "avg_reads", "type": "int"},
    {"source_col": "画像", "target_field": "audience_profile_raw", "type": "str"},
]


def _to_int(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return None


class HuitunImportAdapter:
    source: str = "huitun"
    target_table: str = "blogger"

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        columns = (
            mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
            if mapping is not None
            else _DEFAULT_COLUMNS
        )
        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            t = col.get("type", "str")
            tgt = col["target_field"]
            if t == "int":
                parsed[tgt] = _to_int(raw)
            else:
                parsed[tgt] = str(raw).strip() if raw not in (None, "") else None
        return parsed

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        if not parsed.get("xiaohongshu_id"):
            return ["小红书ID不能为空"]
        return []

    def _build_profile(self, parsed: dict[str, Any]) -> dict:
        profile: dict[str, Any] = {
            "note_stats": {
                "avg_likes": parsed.get("avg_likes") or 0,
                "avg_reads": parsed.get("avg_reads") or 0,
            }
        }
        raw = parsed.get("audience_profile_raw")
        if raw:
            try:
                extra = json.loads(raw)
                if isinstance(extra, dict):
                    profile.update(extra)
            except (ValueError, TypeError):
                pass
        return profile

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        xhs_id = parsed["xiaohongshu_id"]
        blogger = (
            await session.execute(
                select(Blogger).where(
                    Blogger.xiaohongshu_id == xhs_id,
                    Blogger.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if blogger is None:
            await DataQualityService(session).record(
                source="huitun",
                severity="warning",
                message=f"未匹配 blogger: {xhs_id}",
                entity_type="blogger",
                entity_ref=xhs_id,
            )
            return uuid4(), False
        blogger.audience_profile = self._build_profile(parsed)
        await session.flush()
        return blogger.id, False


def register() -> None:
    ImportAdapterRegistry.register(HuitunImportAdapter())


__all__ = ["HuitunImportAdapter", "register"]
