"""导入冲突：记录（导入路径）、查询 / 下载、裁决（8a-6，设计 §4.4、§4.5、§4.5.3）。

- ``ConflictRecorder``：在 runner 的行事务里用。处理任何已有对象时第一步 ``lock_pending``
  （先锁冲突、再锁对象，与裁决路径同序，两者不会互相死锁），之后按本行有没有差异调
  ``touch`` 或 ``record``；两者都只用 ``lock_pending`` 返回的那条、不再自己查库
- ``ImportConflictRepository``：列表筛选、汇总、按批次数待处理条数、按 id 加锁取
- ``ImportConflictService``：列表与 CSV（按查看者字段权限脱敏）、汇总、裁决（单条与多选同一接口，
  整单预检权限，每条一个事务、各自留痕）

「C 是本批次的」= ``ctx.batch_id is not None and C.batch_id == ctx.batch_id``；其余（含
``ctx.batch_id is None`` 的旧路径调用）一律按「别的批次」处理。
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import PermissionDeniedError
from app.core.security.field_permissions import (
    FieldPermissionContext,
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.importer import access
from app.modules.importer.compare import FieldDiff, display_value, normalize
from app.modules.importer.conflict_appliers import (
    APPLIER_VALUE_ERRORS,
    CONFLICT_APPLIERS,
    ApplierValueError,
    invalid_reason,
    spec_map,
    write_object_audit,
)
from app.modules.importer.domain import csv_safe
from app.modules.importer.exceptions import (
    ImportConflictExpectedRequiredError,
    ImportConflictExportTooLargeError,
    ImportConflictFieldPermissionError,
    ImportConflictFieldUnknownError,
    ImportConflictNotFoundError,
)
from app.modules.importer.models import ImportBatch, ImportConflict
from app.modules.importer.outcome import ImportRowContext
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.schemas import (
    ConflictFieldDiff,
    ConflictResolveRequest,
    ConflictResolveResponse,
    ConflictResolveResult,
    ConflictResolveSummary,
    ImportConflictItem,
)

log = logging.getLogger(__name__)

LABEL_MAX = 255
KEY_MAX = 128
EXPORT_LIMIT = 10_000
MASKED_TEXT = "有差异"

_STATUS_LABELS = {
    "pending": "待处理",
    "overwritten": "已用文件覆盖",
    "kept": "已保留系统值",
    "superseded": "被后续导入取代",
    "invalid": "失效",
}
_OBJECT_TYPE_LABELS = {"style": "款式", "sku": "SKU", "goods": "商品", "blogger": "博主"}
_KIND_LABELS = {"fields": "字段差异", "key": "键冲突"}
# 商品冲突在待处理期间单品变成了套装：「用文件覆盖」时转失效的备注与提示（评审 LOW 1）
_GOODS_BECAME_SUIT = "商品已变为套装，不再由导入写入"


def clip_label(text: str, limit: int = LABEL_MAX) -> str:
    """截断显示名：超出时保留前 limit - 1 字加「…」（不让 DataError 让这一行失败）。"""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# ConflictRecorder（导入路径）
# ---------------------------------------------------------------------------


class ConflictRecorder:
    """行事务里记录 / 取代冲突。``touch`` / ``record`` 返回要并进本行的提示。"""

    def __init__(self, session: AsyncSession, ctx: ImportRowContext) -> None:
        self._session = session
        self._ctx = ctx

    def _same_batch(self, c: ImportConflict) -> bool:
        return self._ctx.batch_id is not None and c.batch_id == self._ctx.batch_id

    def _batch_text(self) -> str:
        return str(self._ctx.batch_id) if self._ctx.batch_id is not None else "（无批次）"

    async def lock_pending(self, object_type: str, object_id: UUID) -> ImportConflict | None:
        """取出并锁住该对象在本来源的待处理冲突（至多一条，部分唯一索引保证）。"""
        stmt = (
            select(ImportConflict)
            .where(
                ImportConflict.tenant_id == self._ctx.tenant_id,
                ImportConflict.source == self._ctx.source,
                ImportConflict.object_type == object_type,
                ImportConflict.object_id == object_id,
                ImportConflict.status == "pending",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def touch(
        self,
        c: ImportConflict | None,
        object_type: str,
        object_id: UUID,
        *,
        compared: Iterable[str],
        incoming: Mapping[str, JsonValue],
    ) -> list[str]:
        """本行碰到了这个对象、且没有差异（只有相同或补空）。

        ``incoming``：本行文件值（已 normalize，键 = 字段名）。别的批次的 C 只在它的**每个**字段都被
        本行比较过时才取代（不带字段的键冲突视为满足）；有字段没比较 → C 原样不动。
        """
        if c is None:
            return []
        compared_set = frozenset(compared)
        if self._same_batch(c):
            return self._same_batch_field_warnings(c, compared_set, incoming)
        if all(f.get("field") in compared_set for f in c.fields):
            c.status = "superseded"
            c.resolved_at = _now()
            c.resolution_note = f"被批次 {self._batch_text()} 取代：本次导入这些字段已与系统一致"
            await self._session.flush()
        return []

    async def record(
        self,
        c: ImportConflict | None,
        object_type: str,
        object_id: UUID,
        object_key: str,
        object_label: str,
        *,
        kind: str,
        diffs: Sequence[FieldDiff],
        compared: Iterable[str],
        current: Mapping[str, JsonValue],
        message: str | None,
        current_displays: Mapping[str, str | None] | None = None,
    ) -> list[str]:
        """本行在这个对象上有差异（字段差异或键冲突）。

        ``current``：该对象全部比较字段的当前值（已 normalize），用来重算并入的旧字段；
        ``current_displays``：REF 字段当前值的显示名（其余字段由值生成）。
        """
        compared_set = frozenset(compared)
        new_fields = [d.to_json() for d in diffs]
        if c is None:
            self._insert(
                uuid4(), object_type, object_id, object_key, object_label, kind, new_fields, message
            )
            await self._session.flush()
            return []

        if self._same_batch(c):
            return await self._merge_same_batch(c, kind, new_fields)

        # 别的批次：C 里本行没比较过的字段用当前值重算后并入，已一致的不再列出
        carried: list[dict[str, JsonValue]] = []
        for f in c.fields:
            name = f.get("field")
            if not isinstance(name, str) or name in compared_set:
                continue
            cur = current.get(name)
            if cur == f.get("file"):
                continue
            item = dict(f)
            item["system"] = cur
            item["system_display"] = (current_displays or {}).get(name, display_value(cur))
            item["from_batch_id"] = f.get("from_batch_id") or (
                str(c.batch_id) if c.batch_id is not None else None
            )
            carried.append(item)
        new_id = uuid4()
        c.status = "superseded"
        c.resolved_at = _now()
        c.superseded_by = new_id
        c.resolution_note = f"被批次 {self._batch_text()} 取代"
        await self._session.flush()  # 先放掉部分唯一索引，再插新冲突
        self._insert(
            new_id,
            object_type,
            object_id,
            object_key,
            object_label,
            kind,
            [*new_fields, *carried],
            message,
        )
        await self._session.flush()
        return []

    # -- 内部 --------------------------------------------------------------

    def _insert(
        self,
        conflict_id: UUID,
        object_type: str,
        object_id: UUID,
        object_key: str,
        object_label: str,
        kind: str,
        fields: list[dict[str, JsonValue]],
        message: str | None,
    ) -> None:
        ctx = self._ctx
        self._session.add(
            ImportConflict(
                id=conflict_id,
                tenant_id=ctx.tenant_id,
                source=ctx.source,
                batch_id=ctx.batch_id,
                row_numbers=[ctx.row_number],
                object_type=object_type,
                object_id=object_id,
                object_key=clip_label(object_key, KEY_MAX),
                object_label=clip_label(object_label),
                kind=kind,
                fields=fields,
                message=message,
                status="pending",
                created_by=ctx.actor_id,
            )
        )

    def _same_batch_field_warnings(
        self, c: ImportConflict, compared: frozenset[str], incoming: Mapping[str, JsonValue]
    ) -> list[str]:
        first = c.row_numbers[0] if c.row_numbers else self._ctx.row_number
        warnings: list[str] = []
        for f in c.fields:
            name = f.get("field")
            if name in compared and isinstance(name, str) and incoming.get(name) != f.get("file"):
                warnings.append(
                    f"第 {self._ctx.row_number} 行的{f.get('label')}与第 {first} 行不一致，"
                    f"冲突按第 {first} 行记"
                )
        return warnings

    async def _merge_same_batch(
        self, c: ImportConflict, kind: str, new_fields: list[dict[str, JsonValue]]
    ) -> list[str]:
        row = self._ctx.row_number
        first = c.row_numbers[0] if c.row_numbers else row
        warnings: list[str] = []
        rows = list(c.row_numbers)
        if row not in rows:
            rows.append(row)
        c.row_numbers = rows  # 赋新列表，ORM 才能检测到 JSONB 变更
        if kind != c.kind:
            warnings.append(f"同一批次里该对象第 {first}、{row} 行的信息不一致")
        else:
            fields = [dict(f) for f in c.fields]
            by_name = {f.get("field"): f for f in fields}
            for nf in new_fields:
                existing = by_name.get(nf.get("field"))
                if existing is None:
                    fields.append(nf)
                elif existing.get("file") != nf.get("file"):
                    warnings.append(
                        f"第 {row} 行的{nf.get('label')}与第 {first} 行不一致，冲突按第 {first} 行记"
                    )
            c.fields = fields
        await self._session.flush()
        return warnings


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConflictFilters:
    source: str | None = None
    batch_id: UUID | None = None
    status: str = "pending"  # pending / overwritten / kept / superseded / invalid / all
    field: str | None = None
    object_type: str | None = None


class ImportConflictRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _filtered(
        self, tenant_id: UUID, filters: ConflictFilters, sources: frozenset[str] | None
    ) -> Select[tuple[ImportConflict]]:
        stmt = select(ImportConflict).where(ImportConflict.tenant_id == tenant_id)
        if sources is not None:
            stmt = stmt.where(ImportConflict.source.in_(sorted(sources)))
        if filters.source:
            stmt = stmt.where(ImportConflict.source == filters.source)
        if filters.batch_id is not None:
            stmt = stmt.where(ImportConflict.batch_id == filters.batch_id)
        if filters.status != "all":
            stmt = stmt.where(ImportConflict.status == filters.status)
        if filters.object_type:
            stmt = stmt.where(ImportConflict.object_type == filters.object_type)
        if filters.field:
            # JSONB 包含：fields @> '[{"field": x}]'（绑定参数，不拼 SQL）
            stmt = stmt.where(ImportConflict.fields.contains([{"field": filters.field}]))
        return stmt

    async def list_page(
        self,
        tenant_id: UUID,
        filters: ConflictFilters,
        *,
        sources: frozenset[str] | None,
        page: int,
        page_size: int,
    ) -> tuple[Sequence[ImportConflict], int]:
        stmt = self._filtered(tenant_id, filters, sources)
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar_one()
        )
        stmt = (
            stmt.order_by(ImportConflict.created_at.desc(), ImportConflict.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return (await self._session.execute(stmt)).scalars().all(), total

    async def list_for_export(
        self,
        tenant_id: UUID,
        filters: ConflictFilters,
        *,
        sources: frozenset[str] | None,
        limit: int,
    ) -> Sequence[ImportConflict]:
        """最多取 limit + 1 条（调用方据此判断是否超限）。"""
        stmt = (
            self._filtered(tenant_id, filters, sources)
            .order_by(ImportConflict.created_at.desc(), ImportConflict.id)
            .limit(limit + 1)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_pending(self, tenant_id: UUID, source: str) -> int:
        stmt = select(func.count()).where(
            ImportConflict.tenant_id == tenant_id,
            ImportConflict.source == source,
            ImportConflict.status == "pending",
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def pending_by_batch(self, batch_ids: Iterable[UUID]) -> dict[UUID, int]:
        """一页批次的待处理冲突条数（一条 GROUP BY，不逐行回表）。"""
        ids = list(batch_ids)
        if not ids:
            return {}
        stmt = (
            select(ImportConflict.batch_id, func.count())
            .where(ImportConflict.batch_id.in_(ids), ImportConflict.status == "pending")
            .group_by(ImportConflict.batch_id)
        )
        return {
            bid: int(n) for bid, n in (await self._session.execute(stmt)).all() if bid is not None
        }

    async def get_many(
        self, tenant_id: UUID, ids: Iterable[UUID], *, sources: frozenset[str] | None
    ) -> Sequence[ImportConflict]:
        stmt = select(ImportConflict).where(
            ImportConflict.tenant_id == tenant_id, ImportConflict.id.in_(list(ids))
        )
        if sources is not None:
            stmt = stmt.where(ImportConflict.source.in_(sorted(sources)))
        return (await self._session.execute(stmt)).scalars().all()

    async def lock_by_id(self, conflict_id: UUID) -> ImportConflict | None:
        stmt = (
            select(ImportConflict)
            .where(ImportConflict.id == conflict_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def batch_filenames(self, batch_ids: Iterable[UUID]) -> dict[UUID, str]:
        ids = list(set(batch_ids))
        if not ids:
            return {}
        stmt = select(ImportBatch.id, ImportBatch.original_filename).where(ImportBatch.id.in_(ids))
        return dict((await self._session.execute(stmt)).tuples().all())

    async def user_names(self, user_ids: Iterable[UUID]) -> dict[UUID, str]:
        ids = list(set(user_ids))
        if not ids:
            return {}
        stmt = select(User.id, User.display_name, User.username).where(User.id.in_(ids))
        return {
            uid: (display or username)
            for uid, display, username in (await self._session.execute(stmt)).all()
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _sensitive_of(f: Mapping[str, Any]) -> tuple[str, str] | None:
    sens = f.get("sensitive")
    if isinstance(sens, list | tuple) and len(sens) == 2:
        return str(sens[0]), str(sens[1])
    return None


def mask_fields(
    fields: Iterable[Mapping[str, Any]], field_ctx: FieldPermissionContext
) -> list[ConflictFieldDiff]:
    """查看者没有读权限的受保护字段：system / file / *_display 置空并标 masked。"""
    out: list[ConflictFieldDiff] = []
    for f in fields:
        sens = _sensitive_of(f)
        masked = sens is not None and not can_read_field(sens[0], sens[1], field_ctx)
        out.append(
            ConflictFieldDiff(
                field=str(f.get("field")),
                label=str(f.get("label") or f.get("field")),
                system=None if masked else f.get("system"),
                file=None if masked else f.get("file"),
                system_display=None if masked else f.get("system_display"),
                file_display=None if masked else f.get("file_display"),
                sensitive=sens is not None,
                masked=masked,
                from_batch_id=f.get("from_batch_id"),
            )
        )
    return out


def _readable(sens: tuple[str, str] | None, field_ctx: FieldPermissionContext) -> bool:
    return sens is None or can_read_field(sens[0], sens[1], field_ctx)


def _compare_field_names(sources: Iterable[str]) -> frozenset[str]:
    names: set[str] = set()
    for source in sources:
        adapter = ImportAdapterRegistry.get(source)
        fn = getattr(adapter, "compare_field_names", None)
        if callable(fn):
            names.update(fn())
    return frozenset(names)


class ImportConflictService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ImportConflictRepository(session)
        self._audit = AuditService(session)

    async def _field_ctx(self, user: User) -> FieldPermissionContext:
        return await build_field_perm_context(
            user.id, RoleRepository(self._session), PermissionRepository(self._session)
        )

    def _scope(
        self, filters: ConflictFilters, perms: EffectivePermissions
    ) -> frozenset[str] | None:
        """查看范围：可见来源；指定了来源就校验可见（403）。"""
        sources = access.visible_sources(perms)
        if sources is not None and not sources:
            raise PermissionDeniedError(
                "缺少权限 importer.batch:read",
                details={"required_scope": "importer.batch", "required_action": "read"},
            )
        if filters.source:
            access.require_view(perms, filters.source)
        if filters.field:
            if filters.source:
                candidates: Iterable[str] = [filters.source]
            elif sources is not None:
                candidates = sources
            else:
                candidates = ImportAdapterRegistry.sources()
            if filters.field not in _compare_field_names(candidates):
                raise ImportConflictFieldUnknownError(filters.field)
        return sources

    def _can_resolve(
        self, c: ImportConflict, perms: EffectivePermissions, field_ctx: FieldPermissionContext
    ) -> bool:
        if not perms.has(*access.access_for(c.source).resolve):
            return False
        for f in c.fields:
            sens = _sensitive_of(f)
            if sens and not (
                can_read_field(sens[0], sens[1], field_ctx)
                and can_write_field(sens[0], sens[1], field_ctx)
            ):
                return False
        return True

    # -- 列表 / 汇总 / 下载 ---------------------------------------------------

    async def list_conflicts(
        self,
        filters: ConflictFilters,
        *,
        page: int,
        page_size: int,
        user: User,
        perms: EffectivePermissions,
    ) -> tuple[list[ImportConflictItem], int]:
        """冲突列表：只含可见来源；受保护字段按查看者字段权限脱敏；每条带 can_resolve / overwritable。"""
        sources = self._scope(filters, perms)
        rows, total = await self._repo.list_page(
            user.tenant_id, filters, sources=sources, page=page, page_size=page_size
        )
        field_ctx = await self._field_ctx(user)
        filenames = await self._repo.batch_filenames(c.batch_id for c in rows if c.batch_id)
        names = await self._repo.user_names(c.resolved_by for c in rows if c.resolved_by)
        items = [
            ImportConflictItem(
                id=c.id,
                source=c.source,
                batch_id=c.batch_id,
                batch_filename=filenames.get(c.batch_id) if c.batch_id else None,
                row_numbers=list(c.row_numbers or []),
                object_type=c.object_type,
                object_id=c.object_id,
                object_key=c.object_key,
                object_label=c.object_label,
                kind=c.kind,
                fields=mask_fields(c.fields, field_ctx),
                message=c.message,
                status=c.status,
                created_by=c.created_by,
                created_at=c.created_at,
                resolved_by=c.resolved_by,
                resolved_by_name=names.get(c.resolved_by) if c.resolved_by else None,
                resolved_at=c.resolved_at,
                resolution_note=c.resolution_note,
                superseded_by=c.superseded_by,
                can_resolve=self._can_resolve(c, perms, field_ctx),
                overwritable=c.kind == "fields",
            )
            for c in rows
        ]
        return items, total

    async def summary(self, source: str, user: User, perms: EffectivePermissions) -> int:
        access.require_view(perms, source)
        return await self._repo.count_pending(user.tenant_id, source)

    async def download_csv(
        self, filters: ConflictFilters, user: User, perms: EffectivePermissions
    ) -> bytes:
        """UTF-8 BOM；每个字段差异一行（键冲突另一行）；每个单元格过 ``csv_safe``；脱敏写「有差异」。"""
        sources = self._scope(filters, perms)
        rows = await self._repo.list_for_export(
            user.tenant_id, filters, sources=sources, limit=EXPORT_LIMIT
        )
        if len(rows) > EXPORT_LIMIT:
            raise ImportConflictExportTooLargeError(EXPORT_LIMIT)
        field_ctx = await self._field_ctx(user)
        filenames = await self._repo.batch_filenames(c.batch_id for c in rows if c.batch_id)
        names = await self._repo.user_names(c.resolved_by for c in rows if c.resolved_by)

        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(
            [
                "冲突ID",
                "来源",
                "批次文件",
                "行号",
                "对象类型",
                "对象",
                "对象编码",
                "类型",
                "字段",
                "系统值",
                "文件值",
                "来源批次",
                "状态",
                "处理人",
                "处理时间",
                "说明",
            ]
        )
        for c in rows:
            base = [
                str(c.id),
                access.SOURCE_LABELS.get(c.source, c.source),
                filenames.get(c.batch_id, "") if c.batch_id else "",
                "、".join(str(n) for n in c.row_numbers or []),
                _OBJECT_TYPE_LABELS.get(c.object_type, c.object_type),
                c.object_label,
                c.object_key,
                _KIND_LABELS.get(c.kind, c.kind),
            ]
            tail = [
                _STATUS_LABELS.get(c.status, c.status),
                names.get(c.resolved_by, "") if c.resolved_by else "",
                c.resolved_at.isoformat() if c.resolved_at else "",
            ]
            lines: list[list[Any]] = []
            if c.kind == "key":
                lines.append([*base, "", "", "", "", *tail, c.message or ""])
            for f in mask_fields(c.fields, field_ctx):
                system = MASKED_TEXT if f.masked else f.system_display
                file = MASKED_TEXT if f.masked else f.file_display
                lines.append(
                    [
                        *base,
                        f.label,
                        system,
                        file,
                        str(f.from_batch_id) if f.from_batch_id else "",
                        *tail,
                        c.resolution_note or "",
                    ]
                )
            if not lines:
                lines.append([*base, "", "", "", "", *tail, c.message or ""])
            for line in lines:
                writer.writerow([csv_safe(cell) for cell in line])
        return buf.getvalue().encode("utf-8")

    # -- 裁决 --------------------------------------------------------------

    async def resolve(
        self, payload: ConflictResolveRequest, user: User, perms: EffectivePermissions
    ) -> ConflictResolveResponse:
        """§4.5：404 → 整单预检权限 → 按 id 排序逐条（每条一个事务）→ 返回逐条结果。"""
        if payload.decision == "overwrite":
            missing_expected = [i.id for i in payload.items if i.expected_system_values is None]
            if missing_expected:
                raise ImportConflictExpectedRequiredError(missing_expected)

        ids = [item.id for item in payload.items]
        sources = access.visible_sources(perms)
        found = {c.id: c for c in await self._repo.get_many(user.tenant_id, ids, sources=sources)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ImportConflictNotFoundError(missing)

        # 整单预检：来源权限 + 受保护字段读写权限（任何一条不满足就 403，零改动）
        for source in sorted({c.source for c in found.values()}):
            access.require_resolve(perms, source)
        field_ctx = await self._field_ctx(user)
        denied: list[dict[str, Any]] = []
        for c in found.values():
            for f in c.fields:
                sens = _sensitive_of(f)
                if sens and not (
                    can_read_field(sens[0], sens[1], field_ctx)
                    and can_write_field(sens[0], sens[1], field_ctx)
                ):
                    denied.append({"id": str(c.id), "field": f.get("field")})
        if denied:
            raise ImportConflictFieldPermissionError(denied)

        expected_by_id = {item.id: item.expected_system_values for item in payload.items}
        via = "single" if len(ids) == 1 else "batch"
        # 结束读事务，之后每条一个事务（固定加锁顺序：按 id 排序，冲突 → 对象）
        await self._session.commit()

        results: list[ConflictResolveResult] = []
        for conflict_id in sorted(ids):
            try:
                async with self._session.begin_nested():
                    result = await self._resolve_one(
                        conflict_id,
                        decision=payload.decision,
                        expected=expected_by_id.get(conflict_id) or {},
                        note=payload.note,
                        via=via,
                        user=user,
                        field_ctx=field_ctx,
                    )
                await self._session.commit()
            except Exception:
                # 回滚这一条，已提交的不受影响；继续下一条
                log.exception(
                    "import_conflict_resolve_failed", extra={"conflict_id": str(conflict_id)}
                )
                await self._session.rollback()
                result = ConflictResolveResult(id=conflict_id, outcome="error", message="处理失败")
            results.append(result)

        summary = ConflictResolveSummary()
        for r in results:
            setattr(summary, r.outcome, getattr(summary, r.outcome) + 1)
        log.info("import_conflict_resolve", extra={"counts": summary.model_dump()})
        return ConflictResolveResponse(results=results, summary=summary)

    async def _resolve_one(
        self,
        conflict_id: UUID,
        *,
        decision: str,
        expected: Mapping[str, JsonValue],
        note: str | None,
        via: str,
        user: User,
        field_ctx: FieldPermissionContext,
    ) -> ConflictResolveResult:
        c = await self._repo.lock_by_id(conflict_id)
        if c is None:  # 预检之后被删（批次被清理不会删冲突，这里只是兜底）
            return ConflictResolveResult(id=conflict_id, outcome="gone", message="冲突已不存在")
        if c.status != "pending":
            return ConflictResolveResult(id=c.id, outcome="not_pending", status=c.status)

        if decision == "keep":
            closing = [str(f.get("field")) for f in c.fields] if c.kind == "key" else []
            text = note
            if closing:
                extra = f"并入字段一并关闭：{'、'.join(closing)}"
                text = f"{note}；{extra}" if note else extra
            self._close(c, "kept", user, text)
            await self._audit_resolve(c, decision, via, user)
            return ConflictResolveResult(id=c.id, outcome="resolved", status=c.status)

        # 用文件覆盖
        if c.kind == "key":
            return ConflictResolveResult(
                id=c.id,
                outcome="not_overwritable",
                status=c.status,
                message="键冲突不能用文件覆盖，请到对应页面人工处理",
            )
        applier = CONFLICT_APPLIERS.get(c.object_type)
        if applier is None:
            raise RuntimeError(f"no applier for {c.object_type}")
        obj = await applier.load_for_update(self._session, c.object_id)
        if obj is None:
            c.status = "invalid"
            c.resolved_at = _now()
            c.resolution_note = "对象已删除"
            await self._session.flush()
            log.info("import_conflict_gone", extra={"conflict_id": str(c.id)})
            return ConflictResolveResult(id=c.id, outcome="gone", status=c.status)
        if c.object_type == "goods" and obj.is_suit:
            # 冲突待处理期间单品被加成员变成了套装：导入不改套装（§13.1），照「对象已删除」转失效
            c.status = "invalid"
            c.resolved_at = _now()
            c.resolution_note = _GOODS_BECAME_SUIT
            await self._session.flush()
            log.info("import_conflict_goods_suit", extra={"conflict_id": str(c.id)})
            return ConflictResolveResult(
                id=c.id, outcome="gone", status=c.status, message=_GOODS_BECAME_SUIT
            )

        specs = spec_map(applier)
        names = [str(f.get("field")) for f in c.fields]
        current = applier.current_values(obj, names)
        stale = [n for n in names if normalize(specs[n].kind, expected.get(n)) != current.get(n)]
        if stale:
            masked = [n for n in names if not _readable(specs[n].sensitive, field_ctx)]
            shown = {n: (None if n in masked else v) for n, v in current.items()}
            return ConflictResolveResult(
                id=c.id,
                outcome="stale",
                status=c.status,
                current_values=shown,
                masked_fields=masked,
                message="系统值已被修改，请确认当前值后重新提交",
            )

        values: dict[str, Any] = {}
        for f in c.fields:
            name = str(f.get("field"))
            try:
                values[name] = applier.check(name, f.get("file"))
            except APPLIER_VALUE_ERRORS as exc:
                return ConflictResolveResult(
                    id=c.id,
                    outcome="invalid_value",
                    status=c.status,
                    field=name,
                    message=f"{specs[name].label} 的值不合法（{invalid_reason(exc)}）",
                )
        try:
            await applier.check_refs(self._session, values)
        except APPLIER_VALUE_ERRORS as exc:
            name = exc.field if isinstance(exc, ApplierValueError) else ""
            label = specs[name].label if name in specs else "字段"
            return ConflictResolveResult(
                id=c.id,
                outcome="invalid_value",
                status=c.status,
                field=name or None,
                message=f"{label} 的值不合法（{invalid_reason(exc)}）",
            )
        changes = await applier.apply(self._session, obj, values)
        await write_object_audit(
            self._session,
            applier,
            c.object_id,
            changes,
            via="import_conflict",
            batch_id=c.batch_id,
            user_id=user.id,
            conflict_id=c.id,
        )
        self._close(c, "overwritten", user, note)
        await self._audit_resolve(c, decision, via, user)
        return ConflictResolveResult(id=c.id, outcome="resolved", status=c.status)

    def _close(self, c: ImportConflict, status: str, user: User, note: str | None) -> None:
        c.status = status
        c.resolved_by = user.id
        c.resolved_at = _now()
        c.resolution_note = note

    async def _audit_resolve(self, c: ImportConflict, decision: str, via: str, user: User) -> None:
        await self._audit.log(
            action="import_conflict.resolve",
            resource="import_conflict",
            resource_id=c.id,
            before={"status": "pending"},
            after={
                "decision": decision,
                "status": c.status,
                "source": c.source,
                "object_type": c.object_type,
                "object_id": str(c.object_id),
                "batch_id": str(c.batch_id) if c.batch_id else None,
                "via": via,
            },
            user_id=user.id,
        )
        await self._session.flush()


__all__ = [
    "EXPORT_LIMIT",
    "MASKED_TEXT",
    "ConflictFilters",
    "ConflictRecorder",
    "ImportConflictRepository",
    "ImportConflictService",
    "clip_label",
    "mask_fields",
]
