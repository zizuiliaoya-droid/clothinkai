"""导入相关写入的 applier：导入补空、导入 OVERWRITE、冲突裁决三处共用（8a-6，设计 §4.5.1、§4.5.2）。

一个对象类型一个 applier（``CONFLICT_APPLIERS``）：

- ``load_for_update``：加锁重读（``FOR UPDATE`` + ``populate_existing``）。导入路径在锁之前已用
  按键查询读过这一行，不加 ``populate_existing`` 会拿 identity map 里的旧值去比，锁就白加了
- ``current_values``：读库里的原始值再 ``normalize``，不经任何枚举转换
- ``check``：校验并转成要写入的 Python 值；不合法抛 ``ApplierValueError(field, reason)``，
  ``reason`` 是固定文案、**不含值**
- ``apply``：写入，返回实际写入的 ``{字段: (旧值, 新值)}``（值相同不算写入）

留痕（``build_object_audit``）不复用各模块现有的审计 builder（它们会过滤掉外部图片链接、季节、
品牌等字段）：受保护字段只记 ``after[f"{f}_changed"] = True``，其余字段记前后值；值一律经
``compare.json_value`` 转成 JSON（引擎没有 ``json_serializer``，``Decimal`` / ``UUID`` 原样放进去
会在 flush 时抛 ``TypeError``）。

本 FEAT 只实现博主一个；款式 / SKU / 商品三个随商品资料来源（8a-4）加进 ``CONFLICT_APPLIERS``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import InvalidOperation
from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.blogger.models import Blogger
from app.modules.importer.compare import (
    MONEY_REASON,
    FieldSpec,
    ValueKind,
    check_money,
    json_value,
    normalize,
)

# 裁决 / 导入时一律按「校验不过」处理的异常（导入丢字段、裁决 invalid_value），不落 error
APPLIER_VALUE_ERRORS: tuple[type[Exception], ...] = (
    ValueError,  # ApplierValueError 是它的子类
    InvalidOperation,
    ValidationError,
)

# 非 ApplierValueError 的校验异常一律写这句，不拼 str(exc)（pydantic 的错误文本带 input_value）
GENERIC_INVALID_REASON = "格式不正确"


class ApplierValueError(ValueError):
    """applier 校验不过；``reason`` 是固定文案，不含值。"""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(reason)
        self.field = field
        self.reason = reason


def invalid_reason(exc: BaseException) -> str:
    """校验异常的原因文案：``ApplierValueError`` 用它的固定文案，其余一律「格式不正确」。"""
    return exc.reason if isinstance(exc, ApplierValueError) else GENERIC_INVALID_REASON


class ConflictApplier(Protocol):
    object_type: str
    audit_action: str  # style.update / sku.update / goods.update / blogger.update
    audit_resource: str  # 与手工修改一致：style / sku / goods_main / blogger
    specs: tuple[FieldSpec, ...]

    async def load_for_update(
        self, session: AsyncSession, object_id: UUID, *, include_deleted: bool = False
    ) -> Any | None:
        """加锁重读（FOR UPDATE + populate_existing）。不存在、或已软删且没要 include_deleted → None。"""
        ...

    def current_values(self, obj: Any, names: Iterable[str]) -> dict[str, JsonValue | None]:
        """读库里的原始值再 normalize，不经任何枚举转换。"""
        ...

    def check(self, name: str, value: JsonValue) -> Any:
        """校验并转成要写入的 Python 值；不合法抛 ApplierValueError(field, reason)。"""
        ...

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        """写入，返回实际写入的 {字段: (旧值, 新值)}。"""
        ...


def spec_map(applier: ConflictApplier) -> dict[str, FieldSpec]:
    return {spec.name: spec for spec in applier.specs}


# ---------------------------------------------------------------------------
# 博主
# ---------------------------------------------------------------------------

_INT32_LIMIT = 2**31
_MAX_TAGS = 20


def _text(name: str, value: JsonValue, *, max_len: int | None, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ApplierValueError(name, GENERIC_INVALID_REASON)
    text = value.strip()
    if required and not text:
        raise ApplierValueError(name, "不能为空")
    if max_len is not None and len(text) > max_len:
        raise ApplierValueError(name, f"超过 {max_len} 字")
    return text


class BloggerApplier:
    """博主（``manual_blogger``，设计 §5.5）。补空 / 覆盖 / 裁决都**不重算**博主类型。"""

    object_type = "blogger"
    audit_action = "blogger.update"
    audit_resource = "blogger"
    specs: tuple[FieldSpec, ...] = (
        FieldSpec("nickname", "昵称", ValueKind.TEXT),
        FieldSpec("platform", "平台", ValueKind.TEXT),
        FieldSpec("wechat", "微信", ValueKind.TEXT, sensitive=("blogger", "wechat")),
        FieldSpec("phone", "手机号", ValueKind.TEXT, sensitive=("blogger", "phone")),
        FieldSpec("follower_count", "粉丝数", ValueKind.INT),
        FieldSpec("blogger_type", "博主类型", ValueKind.TEXT),
        FieldSpec("gender_target", "性别投放", ValueKind.TEXT),
        FieldSpec("category_tags", "类目标签", ValueKind.TAGS),
        FieldSpec("quality_tags", "质量标签", ValueKind.TAGS),
        FieldSpec("quote", "报价", ValueKind.DECIMAL, sensitive=("blogger", "quote")),
        FieldSpec("cooperation_history", "合作历史", ValueKind.TEXT),
        FieldSpec("remark", "备注", ValueKind.TEXT),
    )
    # 文本字段的长度上限（None = Text 不限长）
    _TEXT_LIMITS: ClassVar[Mapping[str, int | None]] = {
        "nickname": 128,
        "platform": 16,
        "blogger_type": 16,
        "gender_target": 16,
        "wechat": 64,
        "phone": 32,
        "cooperation_history": None,
        "remark": None,
    }

    async def load_for_update(
        self, session: AsyncSession, object_id: UUID, *, include_deleted: bool = False
    ) -> Blogger | None:
        stmt = select(Blogger).where(Blogger.id == object_id)
        if not include_deleted:
            stmt = stmt.where(Blogger.is_deleted.is_(False))
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await session.execute(stmt)).scalar_one_or_none()

    def current_values(self, obj: Any, names: Iterable[str]) -> dict[str, JsonValue | None]:
        specs = spec_map(self)
        return {name: normalize(specs[name].kind, getattr(obj, name)) for name in names}

    def check(self, name: str, value: JsonValue) -> Any:
        if value is None:
            raise ApplierValueError(name, "不能为空")
        if name in self._TEXT_LIMITS:
            return _text(name, value, max_len=self._TEXT_LIMITS[name], required=name == "nickname")
        if name == "follower_count":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ApplierValueError(name, GENERIC_INVALID_REASON)
            if not 0 <= value < _INT32_LIMIT:
                raise ApplierValueError(name, "必须为 0 到 2147483647 之间的整数")
            return value
        if name == "quote":
            try:
                return check_money(value)
            except ValueError:
                raise ApplierValueError(name, MONEY_REASON) from None
        if name in ("category_tags", "quality_tags"):
            if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                raise ApplierValueError(name, GENERIC_INVALID_REASON)
            tags = [t.strip() for t in value if isinstance(t, str) and t.strip()]
            if len(tags) > _MAX_TAGS:
                raise ApplierValueError(name, f"最多 {_MAX_TAGS} 项")
            return tags
        raise ApplierValueError(name, "不支持的字段")

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        specs = spec_map(self)
        changes: dict[str, tuple[Any, Any]] = {}
        for name, new in values.items():
            old = getattr(obj, name)
            if normalize(specs[name].kind, old) == normalize(specs[name].kind, new):
                continue
            # JSONB 列赋新列表对象，ORM 才能检测到变更
            setattr(obj, name, list(new) if isinstance(new, list) else new)
            changes[name] = (old, new)
        if changes:
            await session.flush()
        return changes


# ---------------------------------------------------------------------------
# 留痕（§4.5.2）
# ---------------------------------------------------------------------------


def build_object_audit(
    applier: ConflictApplier,
    changes: Mapping[str, tuple[Any, Any]],
    *,
    via: str,
    batch_id: UUID | None,
    row_number: int | None = None,
    conflict_id: UUID | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """对象审计的 before / after。受保护字段只记 ``after[f"{f}_changed"] = True``、不进 before；
    值一律经 ``json_value``；``after`` 另带 ``via``、``import_batch_id``，以及 ``row_number``
    （导入路径）或 ``import_conflict_id``（裁决路径）。
    """
    specs = spec_map(applier)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for name, (old, new) in changes.items():
        spec = specs[name]
        if spec.sensitive is not None:
            after[f"{name}_changed"] = True
            continue
        before[name] = json_value(spec.kind, old)
        after[name] = json_value(spec.kind, new)
    after["via"] = via
    after["import_batch_id"] = str(batch_id) if batch_id is not None else None
    if row_number is not None:
        after["row_number"] = row_number
    if conflict_id is not None:
        after["import_conflict_id"] = str(conflict_id)
    return before, after


async def write_object_audit(
    session: AsyncSession,
    applier: ConflictApplier,
    object_id: UUID,
    changes: Mapping[str, tuple[Any, Any]],
    *,
    via: str,
    batch_id: UUID | None,
    user_id: UUID | None,
    actor_type: str | None = None,
    row_number: int | None = None,
    conflict_id: UUID | None = None,
) -> None:
    """写一条对象审计（没有实际写入就不记）。导入路径传 ``actor_type="worker"``、``user_id`` = 导入人。"""
    if not changes:
        return
    before, after = build_object_audit(
        applier,
        changes,
        via=via,
        batch_id=batch_id,
        row_number=row_number,
        conflict_id=conflict_id,
    )
    await AuditService(session).log(
        action=applier.audit_action,
        resource=applier.audit_resource,
        resource_id=object_id,
        before=before,
        after=after,
        user_id=user_id,
        actor_type=actor_type,
    )


CONFLICT_APPLIERS: dict[str, ConflictApplier] = {
    "blogger": BloggerApplier(),
    # 8a-4 再加 style / sku / goods
}


__all__ = [
    "APPLIER_VALUE_ERRORS",
    "CONFLICT_APPLIERS",
    "GENERIC_INVALID_REASON",
    "ApplierValueError",
    "BloggerApplier",
    "ConflictApplier",
    "build_object_audit",
    "invalid_reason",
    "spec_map",
    "write_object_audit",
]
