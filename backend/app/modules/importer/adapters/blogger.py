"""U06c 博主导入适配器（BloggerImportAdapter）。

按 nfr-design-patterns.md P-U06c-01 实现：一行 = 一个 Blogger（单实体）。

关键设计：
- **不经 U03 Service**（Service 自带 commit/audit/字段权限，与 runner per-row 事务边界 FB-C 冲突，
  且 Celery worker 无 HTTP User）→ 直接用 BloggerRepository / ORM
- **不自行 commit**：复用 runner 传入的 session（runner 持有 per-row 事务 + SET LOCAL，NF-1）
- **重复规则按声明**（8a-6，设计 §5.5）：同小红书 ID 不再覆盖。``rule_for("manual_blogger")`` 为
  COMPARE 时全等 → 重复已跳过、系统为空 → 补空、两边都有值且不同 → 持久化冲突；声明换成
  OVERWRITE / KEEP 行为随之改变。新建走 ORM（不再用 ``upsert_atomic``）
- 占位符（``-``、``--``、``—``、``——``）与空格子一样当没给值（N17）
- 多类型解析：list（标签 → JSONB 数组，_split_tags）+ int（follower_count）+ Decimal（quote，禁 float）
- platform 空 → 新建时缺省 "小红书"
- mapping=None 回退内置默认映射（domain-entities §4）
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.models import Blogger
from app.modules.blogger.repository import BloggerRepository
from app.modules.importer.compare import FieldSpec, diff_fields, is_placeholder, normalize
from app.modules.importer.conflict_appliers import (
    APPLIER_VALUE_ERRORS,
    BloggerApplier,
    invalid_reason,
    write_object_audit,
)
from app.modules.importer.conflicts import ConflictRecorder
from app.modules.importer.duplicate_rules import SWITCHABLE_POLICIES, DuplicatePolicy, rule_for
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.outcome import (
    BatchSeen,
    FilledRecord,
    ImportRowContext,
    RowKind,
    RowOutcome,
    merge_kinds,
    screen_batch_seen,
)
from app.modules.importer.registry import ImportAdapterRegistry

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

_OBJECT_TYPE = "blogger"
_APPLIER = BloggerApplier()

# 标签分隔符（中英文分号/逗号）
_TAG_SEP = re.compile(r"[;；,，]")

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "昵称", "target_field": "nickname", "type": "str"},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "微信", "target_field": "wechat", "type": "str"},
    {"source_col": "手机号", "target_field": "phone", "type": "str"},
    {"source_col": "粉丝数", "target_field": "follower_count", "type": "int"},
    {"source_col": "博主类型", "target_field": "blogger_type", "type": "str"},
    {"source_col": "性别投放", "target_field": "gender_target", "type": "str"},
    {"source_col": "类目标签", "target_field": "category_tags", "type": "list"},
    {"source_col": "质量标签", "target_field": "quality_tags", "type": "list"},
    {"source_col": "报价", "target_field": "quote", "type": "decimal"},
    {"source_col": "合作历史", "target_field": "cooperation_history", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("xiaohongshu_id", "小红书ID"),
    ("nickname", "昵称"),
)
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("xiaohongshu_id", 64),
    ("nickname", 128),
    ("wechat", 64),
    ("phone", 32),
    ("platform", 16),
    ("blogger_type", 16),
    ("gender_target", 16),
)


def _split_tags(raw: Any) -> list[str]:
    """分隔字符串（;；,，）→ 拆分 + strip + 去空、去占位符 → list。空 → []。"""
    if raw is None or str(raw).strip() == "":
        return []
    return [t.strip() for t in _TAG_SEP.split(str(raw)) if not is_placeholder(t)]


def _to_int(raw: Any) -> int | str | None:
    """去千分位 + int。非法值保留原串供 validate。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return str(raw)


def _to_decimal(raw: Any) -> Decimal | str | None:
    """去千分位 + Decimal（禁 float）。非法值保留原串。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return str(raw)


class BloggerImportAdapter:
    """manual_blogger 导入适配器（一行 → 一个 Blogger upsert）。"""

    source: str = "manual_blogger"
    target_table: str = "blogger"
    # 受字段权限保护的目标字段 → FIELD_PERMISSION_REGISTRY 的 (entity, field)：
    # 失败明细 CSV 按查看者的读权限遮挡对应原始列（importer/masking.py）
    sensitive_targets: ClassVar[Mapping[str, tuple[str, str]]] = {
        "quote": ("blogger", "quote"),
        "wechat": ("blogger", "wechat"),
        "phone": ("blogger", "phone"),
    }
    # 可切换的重复策略（duplicate_rules 的声明须在其内，护栏测试会查）
    supported_policies: ClassVar[frozenset[DuplicatePolicy]] = SWITCHABLE_POLICIES

    def builtin_columns(self) -> list[dict[str, Any]]:
        """内置默认映射。"""
        return _DEFAULT_COLUMNS

    # ----------------------- parse_row（纯函数）----------------------- #

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        """按 mapping（或内置默认）映射表头 + 多类型转换。"""
        if mapping is not None:
            columns = mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
        else:
            columns = _DEFAULT_COLUMNS

        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            # 占位符（-、--、—、——）与空格子一样当没给值（§5.5，N17）：新建不写进库、已有不补空也不冲突
            if is_placeholder(raw):
                raw = None
            target = col["target_field"]
            col_type = col.get("type", "str")
            if col_type == "list":
                parsed[target] = _split_tags(raw) or None
            elif col_type == "int":
                parsed[target] = _to_int(raw)
            elif col_type == "decimal":
                parsed[target] = _to_decimal(raw)
            else:
                parsed[target] = str(raw).strip() if raw not in (None, "") else None
        return parsed

    # ----------------------- validate（纯函数）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空=通过）。"""
        errs: list[str] = []
        for field, label in _REQUIRED:
            if not parsed.get(field):
                errs.append(f"{label}不能为空")
        follower = parsed.get("follower_count")
        if follower is not None and (not isinstance(follower, int) or follower < 0):
            errs.append("粉丝数必须为非负整数")
        quote = parsed.get("quote")
        if quote is not None and (not isinstance(quote, Decimal) or quote < 0):
            errs.append("报价必须为非负数字")
        for field, max_len in _MAX_LEN:
            value = parsed.get(field)
            if value and isinstance(value, str) and len(value) > max_len:
                errs.append(f"{field} 超过长度上限 {max_len}")
        return errs

    # ----------------------- 比较字段（8a-6）----------------------- #

    def compare_specs(self) -> tuple[FieldSpec, ...]:
        """比较用的字段（§5.5 的表；小红书 ID 是判重键，不比较）。"""
        return _APPLIER.specs

    def compare_field_names(self) -> frozenset[str]:
        """冲突按字段筛选时认的字段名。"""
        return frozenset(spec.name for spec in _APPLIER.specs)

    # ----------------------- upsert（复用 runner session，不 commit）----------------------- #

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """旧协议入口（直接调用的老测试与脚本）：用 ``batch_id=None`` 与新建的 ``BatchSeen`` 调
        ``upsert_with_context`` 再还原成 ``(resource_id, is_inserted)``。不自行 commit。
        """
        ctx = ImportRowContext(
            tenant_id=tenant_id,
            source=self.source,
            batch_id=None,
            row_number=0,
            actor_id=actor_id,
            batch_seen=BatchSeen(),
        )
        outcome = await self.upsert_with_context(parsed, session=session, ctx=ctx)
        assert outcome.resource_id is not None
        return outcome.resource_id, outcome.kind is RowKind.INSERTED

    async def upsert_with_context(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
    ) -> RowOutcome:
        """按 ``rule_for(source)`` 处理一行（8a-6，设计 §5.5、§5.2 的固定顺序）。

        - 没有该小红书 ID 的博主（软删的不算）→ ORM 新建（字段值与原来的导入一致：平台空缺省
          「小红书」、标签空为 []、博主类型原样），写入后登记比较字段
        - 已有：KEEP 不加锁直接「重复已跳过」；COMPARE / OVERWRITE 先锁冲突、再加锁重读博主，锁内
          ``screen_batch_seen`` → ``diff_fields``；补空 / 覆盖经 applier 写入并留痕；有冲突 → record，
          否则 touch。补空 / 覆盖 / 裁决都**不重算**博主类型
        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        """
        repo = BloggerRepository(session)
        xhs_id = parsed["xiaohongshu_id"]
        specs = _APPLIER.specs
        incoming = {spec.name: parsed[spec.name] for spec in specs if spec.name in parsed}

        existing = await repo.get_by_xiaohongshu_id(xhs_id, include_deleted=False)
        if existing is None:
            return await self._insert(parsed, session=session, ctx=ctx)

        rule = rule_for(ctx.source)
        if rule.policy is DuplicatePolicy.KEEP:
            return RowOutcome(resource_id=existing.id, kind=RowKind.SKIPPED)

        recorder = ConflictRecorder(session, ctx)
        conflict = await recorder.lock_pending(_OBJECT_TYPE, existing.id)
        blogger = await _APPLIER.load_for_update(session, existing.id)
        if blogger is None:
            raise RowValidationError(f"博主 {xhs_id} 在导入期间被删除，请重试")

        names = [spec.name for spec in specs]
        current = _APPLIER.current_values(blogger, names)
        screened, warnings = screen_batch_seen(ctx, _OBJECT_TYPE, blogger.id, specs, incoming)
        diff = diff_fields(specs, current, screened, fill_empty=rule.fill_empty)
        label = blogger.nickname
        screened_norm = {
            spec.name: normalize(spec.kind, screened[spec.name])
            for spec in specs
            if spec.name in screened
        }

        if rule.policy is DuplicatePolicy.OVERWRITE:
            to_write = {**diff.fills, **{d.field: d.file for d in diff.conflicts}}
            changes = await self._write(
                blogger,
                to_write,
                session=session,
                ctx=ctx,
                via="import_overwrite",
                warnings=warnings,
            )
            warnings += await recorder.touch(
                conflict, _OBJECT_TYPE, blogger.id, compared=diff.compared, incoming=screened_norm
            )
            kind = RowKind.UPDATED if changes else RowKind.SKIPPED
            return RowOutcome(resource_id=blogger.id, kind=kind, warnings=warnings)

        # COMPARE：补空照做，冲突只含两边都有值且不同的字段
        changes = await self._write(
            blogger,
            dict(diff.fills),
            session=session,
            ctx=ctx,
            via="import_fill",
            warnings=warnings,
        )
        filled = [FilledRecord(_OBJECT_TYPE, label, list(changes))] if changes else []
        kinds = [RowKind.FILLED] if changes else [RowKind.SKIPPED]
        if diff.conflicts:
            warnings += await recorder.record(
                conflict,
                _OBJECT_TYPE,
                blogger.id,
                xhs_id,
                label,
                kind="fields",
                diffs=diff.conflicts,
                compared=diff.compared,
                current=current,
                message=None,
            )
            kinds.append(RowKind.CONFLICT)
        else:
            warnings += await recorder.touch(
                conflict, _OBJECT_TYPE, blogger.id, compared=diff.compared, incoming=screened_norm
            )
        return RowOutcome(
            resource_id=blogger.id, kind=merge_kinds(kinds), warnings=warnings, filled=filled
        )

    async def _insert(
        self, parsed: dict[str, Any], *, session: AsyncSession, ctx: ImportRowContext
    ) -> RowOutcome:
        values: dict[str, Any] = {
            "xiaohongshu_id": parsed["xiaohongshu_id"],
            "nickname": parsed["nickname"],
            "platform": parsed.get("platform") or "小红书",
            "wechat": parsed.get("wechat"),
            "phone": parsed.get("phone"),
            "follower_count": parsed.get("follower_count"),
            "blogger_type": parsed.get("blogger_type"),
            "gender_target": parsed.get("gender_target"),
            "category_tags": parsed.get("category_tags") or [],
            "quality_tags": parsed.get("quality_tags") or [],
            "quote": parsed.get("quote"),
            "cooperation_history": parsed.get("cooperation_history"),
            "remark": parsed.get("remark"),
        }
        blogger = Blogger(tenant_id=ctx.tenant_id, **values)
        session.add(blogger)
        await session.flush()
        # 新对象的 id 不可能已登记过：这一步只为登记，同批后面的行以本行写入的值为准
        screen_batch_seen(ctx, _OBJECT_TYPE, blogger.id, _APPLIER.specs, values)
        return RowOutcome(resource_id=blogger.id, kind=RowKind.INSERTED)

    async def _write(
        self,
        blogger: Blogger,
        to_write: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
        via: str,
        warnings: list[str],
    ) -> dict[str, tuple[Any, Any]]:
        """经 applier 校验并写入；不合法的字段丢弃、加提示、撤回 batch_seen 登记（行不失败）。"""
        if not to_write:
            return {}
        labels = {spec.name: spec.label for spec in _APPLIER.specs}
        values: dict[str, Any] = {}
        for name, value in to_write.items():
            try:
                values[name] = _APPLIER.check(name, value)
            except APPLIER_VALUE_ERRORS as exc:
                warnings.append(f"{labels[name]} 的值不合法（{invalid_reason(exc)}），未写入")
                ctx.batch_seen.staged.pop((_OBJECT_TYPE, blogger.id, name), None)
        changes = await _APPLIER.apply(session, blogger, values)
        await write_object_audit(
            session,
            _APPLIER,
            blogger.id,
            changes,
            via=via,
            batch_id=ctx.batch_id,
            user_id=ctx.actor_id,
            actor_type="worker",
            row_number=ctx.row_number,
        )
        return changes


def register() -> None:
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(BloggerImportAdapter())


__all__ = ["BloggerImportAdapter", "register"]
