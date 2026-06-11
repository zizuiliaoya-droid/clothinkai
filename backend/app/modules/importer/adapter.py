"""U06a ImportAdapter 协议（FB-C）。

框架/适配器解耦：U06a 定义协议 + Registry；具体业务 Adapter 由 U06b/c/d/e 实现并注册。

关键签名（FB-C）：``upsert`` 接收 ``session / tenant_id / actor_id``（不是 HTTP User），
因为 run_import_batch 跑在 Celery worker（无 CurrentActiveUser），且 runner 统一持有
per-row 事务边界（adapter 不自行 commit）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping


@runtime_checkable
class ImportAdapter(Protocol):
    """每个业务来源实现一个适配器（U06b/c/d/e）。"""

    source: str          # 来源标识（注册键），如 "manual_style_sku"
    target_table: str    # 目标表名（审计 / 展示）

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        """按 field_mapping 把原始列名映射成目标字段 + 类型转换。

        纯函数，不碰 DB。mapping 为 None 时按恒等映射（原样）。
        """
        ...

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空 = 通过）。纯函数。"""
        ...

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """幂等 upsert（按业务键）。返回 (resource_id, is_inserted)。

        **不自行 commit** — 事务边界由 runner 控制（每行独立事务，FB-C）。
        session 已由 runner 设置 ``SET LOCAL app.tenant_id``（NF-1），受 RLS 约束。
        """
        ...


__all__ = ["ImportAdapter"]
