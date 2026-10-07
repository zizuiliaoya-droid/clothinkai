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

``check`` 只做不查库的校验；需要查库的规则（商品品牌存在且启用）在 ``check_refs``，调用方在
``check`` 之后、``apply`` 之前调，抛出的 ``ApplierValueError`` 按同样的「校验不过」处理。

博主（8a-6）、款式 / SKU / 商品（8a-4，商品资料来源）四个 applier。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import InvalidOperation
from typing import Any, ClassVar, Protocol, cast
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
from app.modules.product.goods_models import GoodsMain
from app.modules.product.goods_schemas import GOODS_SHORT_NAME_MAX_LEN
from app.modules.product.images import normalize_external_image_url
from app.modules.product.models import Brand, Sku, Style

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

    async def check_refs(self, session: AsyncSession, values: Mapping[str, Any]) -> None:
        """需要查库的校验（``check`` 之后、``apply`` 之前）；不合法抛 ApplierValueError。"""
        ...

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        """写入，返回实际写入的 {字段: (旧值, 新值)}。"""
        ...


def spec_map(applier: ConflictApplier) -> dict[str, FieldSpec]:
    return {spec.name: spec for spec in applier.specs}


async def _load_locked(
    session: AsyncSession, model: Any, object_id: UUID, *, include_deleted: bool
) -> Any | None:
    """四个 applier 共用的加锁重读：``FOR UPDATE`` + ``populate_existing``（§4.5.1）。"""
    stmt = select(model).where(model.id == object_id)
    if not include_deleted:
        stmt = stmt.where(model.is_deleted.is_(False))
    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _apply_values(
    applier: ConflictApplier, session: AsyncSession, obj: Any, values: Mapping[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """按 ``normalize`` 比较后写入（值相同不算写入）；有写入才 flush。"""
    specs = spec_map(applier)
    changes: dict[str, tuple[Any, Any]] = {}
    for name, new in values.items():
        old = getattr(obj, name)
        if normalize(specs[name].kind, old) == normalize(specs[name].kind, new):
            continue
        setattr(obj, name, new)
        changes[name] = (old, new)
    if changes:
        await session.flush()
    return changes


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
        obj = await _load_locked(session, Blogger, object_id, include_deleted=include_deleted)
        return cast("Blogger | None", obj)

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

    async def check_refs(self, session: AsyncSession, values: Mapping[str, Any]) -> None:
        """博主没有引用字段。"""
        return

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
# 款式 / SKU / 商品（商品资料来源 manual_style_sku，8a-4）
# ---------------------------------------------------------------------------

EXTERNAL_IMAGE_REASON = "不是 http/https 地址或超过 1024 字符"
BRAND_REASON = "品牌不存在或已停用"
# 导入口径（自产 / 采购 / 代发）与系统枚举（自产 / 外采 / 混合）的并集；两边不一致是现状问题
SKU_SOURCING_VALUES: frozenset[str] = frozenset({"自产", "外采", "混合", "采购", "代发"})
_SKU_MONEY_FIELDS = frozenset({"base_price", "cost_price", "purchase_price", "tag_price"})


class StyleApplier:
    """款式：只有外部图片链接一个比较字段（「商品名称」仅新建时写入，不比较）。"""

    object_type = "style"
    audit_action = "style.update"
    audit_resource = "style"
    specs: tuple[FieldSpec, ...] = (FieldSpec("external_image_url", "图片", ValueKind.TEXT),)

    async def load_for_update(
        self, session: AsyncSession, object_id: UUID, *, include_deleted: bool = False
    ) -> Style | None:
        obj = await _load_locked(session, Style, object_id, include_deleted=include_deleted)
        return cast("Style | None", obj)

    def current_values(self, obj: Any, names: Iterable[str]) -> dict[str, JsonValue | None]:
        specs = spec_map(self)
        return {name: normalize(specs[name].kind, getattr(obj, name)) for name in names}

    def check(self, name: str, value: JsonValue) -> Any:
        if name != "external_image_url":
            raise ApplierValueError(name, "不支持的字段")
        url = normalize_external_image_url(value)
        if url is None:
            raise ApplierValueError(name, EXTERNAL_IMAGE_REASON)
        return url

    async def check_refs(self, session: AsyncSession, values: Mapping[str, Any]) -> None:
        return

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        return await _apply_values(self, session, obj, values)


class SkuApplier:
    """SKU：颜色、规格、四个价格、货源类型。

    **不调用** ``validate_sku_sourcing_price`` / ``validate_sku_prices``：前者对存量「采购 / 代发」
    会抛 ``ValueError``，还强制「自产必须有成本价」，补空 / 覆盖不能比导入新建更严（§4.5.1）。
    读当前值一律用库里的原始字符串，不经 ``SourcingType(...)``。
    """

    object_type = "sku"
    audit_action = "sku.update"
    audit_resource = "sku"
    specs: tuple[FieldSpec, ...] = (
        FieldSpec("color", "颜色", ValueKind.TEXT),
        FieldSpec("size", "规格", ValueKind.TEXT),
        FieldSpec("base_price", "基本售价", ValueKind.DECIMAL),
        FieldSpec("cost_price", "成本价", ValueKind.DECIMAL, sensitive=("sku", "cost_price")),
        FieldSpec(
            "purchase_price", "采购价", ValueKind.DECIMAL, sensitive=("sku", "purchase_price")
        ),
        FieldSpec("tag_price", "市场|吊牌价", ValueKind.DECIMAL),
        FieldSpec("sourcing_type", "货源类型", ValueKind.TEXT),
    )
    _TEXT_LIMITS: ClassVar[Mapping[str, int]] = {"color": 64, "size": 32}

    async def load_for_update(
        self, session: AsyncSession, object_id: UUID, *, include_deleted: bool = False
    ) -> Sku | None:
        obj = await _load_locked(session, Sku, object_id, include_deleted=include_deleted)
        return cast("Sku | None", obj)

    def current_values(self, obj: Any, names: Iterable[str]) -> dict[str, JsonValue | None]:
        specs = spec_map(self)
        return {name: normalize(specs[name].kind, getattr(obj, name)) for name in names}

    def check(self, name: str, value: JsonValue) -> Any:
        if value is None:
            raise ApplierValueError(name, "不能为空")
        if name in self._TEXT_LIMITS:
            return _text(name, value, max_len=self._TEXT_LIMITS[name], required=True)
        if name in _SKU_MONEY_FIELDS:
            try:
                return check_money(value)
            except ValueError:
                raise ApplierValueError(name, MONEY_REASON) from None
        if name == "sourcing_type":
            text = _text(name, value, max_len=None, required=True)
            if text not in SKU_SOURCING_VALUES:
                raise ApplierValueError(name, "必须为 自产/外采/混合/采购/代发 之一")
            return text
        raise ApplierValueError(name, "不支持的字段")

    async def check_refs(self, session: AsyncSession, values: Mapping[str, Any]) -> None:
        return

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        return await _apply_values(self, session, obj, values)


class GoodsApplier:
    """商品（只写单品，套装永不经导入修改，§5.3）：简称、品牌、季节。

    ``load_for_update(include_deleted=True)`` 只给键冲突的 ``code_goods`` 用（它可能已软删）。
    审计 ``goods.update`` 的 before / after 在 ``brand_id`` 之外另带 ``brand_name``（§4.5.2）。
    """

    object_type = "goods"
    audit_action = "goods.update"
    audit_resource = "goods_main"
    specs: tuple[FieldSpec, ...] = (
        FieldSpec("short_name", "商品简称", ValueKind.TEXT),
        FieldSpec("brand_id", "品牌", ValueKind.REF),
        FieldSpec("season", "季节", ValueKind.TEXT),
    )
    _TEXT_LIMITS: ClassVar[Mapping[str, int]] = {
        "short_name": GOODS_SHORT_NAME_MAX_LEN,
        "season": 64,
    }

    async def load_for_update(
        self, session: AsyncSession, object_id: UUID, *, include_deleted: bool = False
    ) -> GoodsMain | None:
        obj = await _load_locked(session, GoodsMain, object_id, include_deleted=include_deleted)
        return cast("GoodsMain | None", obj)

    def current_values(self, obj: Any, names: Iterable[str]) -> dict[str, JsonValue | None]:
        specs = spec_map(self)
        return {name: normalize(specs[name].kind, getattr(obj, name)) for name in names}

    def check(self, name: str, value: JsonValue) -> Any:
        if value is None:
            raise ApplierValueError(name, "不能为空")
        if name in self._TEXT_LIMITS:
            return _text(name, value, max_len=self._TEXT_LIMITS[name], required=True)
        if name == "brand_id":
            try:
                return UUID(str(value))
            except ValueError:
                raise ApplierValueError(name, BRAND_REASON) from None
        raise ApplierValueError(name, "不支持的字段")

    async def check_refs(self, session: AsyncSession, values: Mapping[str, Any]) -> None:
        """品牌存在且启用（与原商品接口的品牌校验同规则；裁决时品牌可能已被停用）。"""
        brand_id = values.get("brand_id")
        if brand_id is None:
            return
        brand = await session.get(Brand, brand_id)
        if brand is None or not brand.is_active:
            raise ApplierValueError("brand_id", BRAND_REASON)

    async def apply(
        self, session: AsyncSession, obj: Any, values: dict[str, Any]
    ) -> dict[str, tuple[Any, Any]]:
        return await _apply_values(self, session, obj, values)

    async def audit_extras(
        self, session: AsyncSession, changes: Mapping[str, tuple[Any, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """``brand_id`` 有变更时 before / after 各带 ``brand_name``（字符串或 null）。"""
        if "brand_id" not in changes:
            return {}, {}
        old, new = changes["brand_id"]
        return {"brand_name": await _brand_name(session, old)}, {
            "brand_name": await _brand_name(session, new)
        }


async def _brand_name(session: AsyncSession, brand_id: Any) -> str | None:
    if brand_id is None:
        return None
    brand = await session.get(Brand, brand_id)
    return str(brand.brand_name) if brand is not None else None


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
    # 引用字段的显示名（商品品牌带 brand_name）；只有声明了 audit_extras 的 applier 才有
    extras = getattr(applier, "audit_extras", None)
    if extras is not None:
        extra_before, extra_after = await extras(session, changes)
        before.update(extra_before)
        after.update(extra_after)
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
    "style": StyleApplier(),
    "sku": SkuApplier(),
    "goods": GoodsApplier(),
}


__all__ = [
    "APPLIER_VALUE_ERRORS",
    "BRAND_REASON",
    "CONFLICT_APPLIERS",
    "EXTERNAL_IMAGE_REASON",
    "GENERIC_INVALID_REASON",
    "SKU_SOURCING_VALUES",
    "ApplierValueError",
    "BloggerApplier",
    "ConflictApplier",
    "GoodsApplier",
    "SkuApplier",
    "StyleApplier",
    "build_object_audit",
    "invalid_reason",
    "spec_map",
    "write_object_audit",
]
