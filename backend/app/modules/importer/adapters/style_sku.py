"""U06b / 8a-4 商品资料导入适配器（StyleSkuImportAdapter，来源 manual_style_sku）。

一行 = 一个 SKU（设计 §5）：款式（款号判重）→ SKU（SKU 编码判重）→ 商品层（§5.3 决定写哪个
单品商品或顺手新建单品商品）。

关键设计：
- **不经 U02 Service**（Service 自带 commit / audit / 权限，与 runner 的逐行事务冲突，且 worker
  没有 HTTP User）→ 直接用仓储与 ORM；**不自行 commit**
- 内置默认映射由目标字段目录 ``mapping_targets`` 生成（对齐聚水潭 42 列与精简导出）；类目不再读、
  不再写「未分类」；「商品名称」标 ``create_only``（只在新建款式 / 单品商品时写入）
- 款式层不再写简称 / 季节 / 品牌 / 类目（J8）：简称 / 季节 / 品牌写商品层
- 重复规则按声明（``rule_for``）：COMPARE / OVERWRITE / KEEP；已有 SKU 不再被同编码覆盖。
  已有对象一律按固定顺序处理：先锁冲突、再加锁重读对象、锁内比较（§5.2）
- 第 0 步：款式要新建而 SKU 编码已属于别的款式 → 不建款式、不建单品商品，只对 SKU 记键冲突
- 套装永远不会被导入修改；任何已有商品编码都不会被修改（§5.3）
- 占位符（``-``、``--``、``—``、``——``）与空格子一样当没给值；金额去千分位与空白
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.importer.adapters.style_sku_goods import (
    GoodsAction,
    create_single_goods,
    decide_goods_target,
    load_goods_context,
)
from app.modules.importer.compare import (
    MONEY_REASON,
    FieldSpec,
    check_money,
    diff_fields,
    is_placeholder,
    normalize,
)
from app.modules.importer.conflict_appliers import (
    APPLIER_VALUE_ERRORS,
    ApplierValueError,
    ConflictApplier,
    GoodsApplier,
    SkuApplier,
    StyleApplier,
    invalid_reason,
    write_object_audit,
)
from app.modules.importer.conflicts import ConflictRecorder
from app.modules.importer.domain import TargetSpec, builtin_columns_from_targets
from app.modules.importer.duplicate_rules import (
    SWITCHABLE_POLICIES,
    DuplicatePolicy,
    DuplicateRule,
    rule_for,
)
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
from app.modules.product.goods_schemas import GOODS_SHORT_NAME_MAX_LEN, goods_display_name
from app.modules.product.images import normalize_external_image_url
from app.modules.product.models import Brand, Sku, Style
from app.modules.product.repository import SkuRepository, StyleRepository

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

_STYLE = StyleApplier()
_SKU = SkuApplier()
_GOODS = GoodsApplier()

# 目标字段目录 = 内置默认映射（设计 §5.1）。目标字段名沿用旧内置映射（brand_code、season 不改名），
# 库里已有的自定义映射照常生效；唯一被忽略的旧目标字段是 category（FR-3.2）
MAPPING_TARGETS: tuple[TargetSpec, ...] = (
    TargetSpec("style_code", "款式编码", "str", "款式编码", ("款号", "货号"), required=True),
    TargetSpec("sku_code", "商品编码", "str", "商品编码", ("SKU编码", "规格编码"), required=True),
    TargetSpec(
        "style_name",
        "商品名称",
        "str",
        "商品名称",
        ("款式名称", "款名", "品名"),
        required=True,
        create_only=True,
    ),
    TargetSpec("color_size", "颜色及规格", "str", "颜色及规格", group="color_size:combined"),
    TargetSpec("color", "颜色", "str", "颜色", group="color_size:split"),
    TargetSpec("size", "规格", "str", "规格", ("尺码",), group="color_size:split"),
    TargetSpec("base_price", "基本售价", "decimal", "基本售价", ("吊牌价",)),
    TargetSpec("cost_price", "成本价", "decimal", "成本价", sensitive=("sku", "cost_price")),
    TargetSpec(
        "purchase_price", "采购价", "decimal", "采购价", sensitive=("sku", "purchase_price")
    ),
    TargetSpec("tag_price", "市场|吊牌价", "decimal", "市场|吊牌价", ("市场吊牌价", "市场吊牌")),
    TargetSpec("sourcing_type", "货源类型", "str", "货源类型"),
    TargetSpec("goods_short_name", "商品简称", "str", "商品简称", ("简称",)),
    TargetSpec("brand_code", "品牌", "str", "品牌", ("品牌编码",)),
    TargetSpec("season", "季节", "str", "季节"),
    TargetSpec("external_image_url", "图片", "str", "图片", ("图片链接", "主图")),
)
_TARGETS_BY_FIELD: dict[str, TargetSpec] = {t.field: t for t in MAPPING_TARGETS}
_BUILTIN_COLUMNS: list[dict[str, Any]] = builtin_columns_from_targets(MAPPING_TARGETS)

_REQUIRED: tuple[str, ...] = ("style_code", "style_name", "sku_code", "color", "size")
_MONEY_FIELDS: tuple[str, ...] = ("base_price", "cost_price", "purchase_price", "tag_price")
_SOURCING = frozenset({"自产", "采购", "代发"})
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("style_code", 64),
    ("style_name", 255),
    ("sku_code", 64),
    ("color", 64),
    ("size", 32),
)
_SEASON_MAX_LEN = 64
_BRAND_MAX_LEN = 128
_COLOR_SIZE_SEPARATORS = (";", "；", ",", "，", "/", " ")


def _label(field: str) -> str:
    return _TARGETS_BY_FIELD[field].label


def _to_decimal(raw: Any) -> Decimal | str | None:
    """去千分位与空白后转 Decimal（禁 float）。非法值保留原串供 validate 检出。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = "".join(str(raw).replace(",", "").split())
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return str(raw)


def _style_label(style: Style) -> str:
    return f"{style.style_code} {style.style_name}"


def _sku_label(sku: Sku) -> str:
    return f"{sku.sku_code}（{sku.color} {sku.size}）"


class StyleSkuImportAdapter:
    """manual_style_sku 导入适配器（一行 → 款式 / SKU / 单品商品）。"""

    source: str = "manual_style_sku"
    target_table: str = "style+sku"
    mapping_targets: ClassVar[tuple[TargetSpec, ...]] = MAPPING_TARGETS
    # 受字段权限保护的目标字段 → FIELD_PERMISSION_REGISTRY 的 (entity, field)：
    # 失败明细 CSV 按查看者的读权限遮挡对应原始列（importer/masking.py）
    sensitive_targets: ClassVar[Mapping[str, tuple[str, str]]] = {
        t.field: t.sensitive for t in MAPPING_TARGETS if t.sensitive is not None
    }
    # 可切换的重复策略（duplicate_rules 的声明须在其内，护栏测试会查）
    supported_policies: ClassVar[frozenset[DuplicatePolicy]] = SWITCHABLE_POLICIES

    def builtin_columns(self) -> list[dict[str, Any]]:
        """内置默认映射（由目录生成，含别名）。"""
        return _BUILTIN_COLUMNS

    # ----------------------- parse_row（纯函数）----------------------- #

    def _columns(self, mapping: FieldMapping | None) -> Sequence[Mapping[str, Any]]:
        """有自定义映射时只取目录里认识的目标字段（旧映射里的 category 被忽略）。"""
        if mapping is None:
            return _BUILTIN_COLUMNS
        custom = mapping.mapping_config.get("columns") or []
        known = [c for c in custom if c.get("target_field") in _TARGETS_BY_FIELD]
        return known or _BUILTIN_COLUMNS

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        """按映射（或内置默认）取值：占位符当没给值、主列没值再试别名；类型由目录定。"""
        parsed: dict[str, Any] = {}
        for col in self._columns(mapping):
            target = str(col["target_field"])
            raw = row.get(col["source_col"])
            if is_placeholder(raw):
                raw = None
                for alias in col.get("aliases") or []:
                    if not is_placeholder(row.get(alias)):
                        raw = row.get(alias)
                        break
            if _TARGETS_BY_FIELD[target].type == "decimal":
                parsed[target] = _to_decimal(raw)
            else:
                parsed[target] = str(raw).strip() if raw is not None else None
        # 颜色 / 规格缺失时由「颜色及规格」拆分（如「深灰色;L」/「深灰色，L」）
        cs = parsed.get("color_size")
        if cs and (not parsed.get("color") or not parsed.get("size")):
            for sep in _COLOR_SIZE_SEPARATORS:
                if sep in cs:
                    parts = [p.strip() for p in cs.split(sep) if p.strip()]
                    if len(parts) >= 2:
                        if not parsed.get("color"):
                            parsed["color"] = parts[0]
                        if not parsed.get("size"):
                            parsed["size"] = parts[1]
                    break
        return parsed

    # ----------------------- validate（纯函数）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """必填与长度、价格（格式错或 ≥ 1 亿整行失败）、货源类型。文案不含受保护字段的值。"""
        errs: list[str] = []
        for name in _REQUIRED:
            if not parsed.get(name):
                errs.append(f"{_label(name)}不能为空")
        for name in _MONEY_FIELDS:
            value = parsed.get(name)
            if value is None:
                continue
            try:
                check_money(value)
            except ValueError:
                errs.append(f"{_label(name)}{MONEY_REASON}")
        sourcing = parsed.get("sourcing_type")
        if sourcing and sourcing not in _SOURCING:
            errs.append("货源类型必须为 自产/采购/代发 之一")
        for name, max_len in _MAX_LEN:
            value = parsed.get(name)
            if value and len(value) > max_len:
                errs.append(f"{_label(name)}超过长度上限 {max_len}")
        return errs

    # ----------------------- 选填项（§5.4）----------------------- #

    async def sanitize_optional(
        self, parsed: dict[str, Any], *, session: AsyncSession, tenant_id: UUID
    ) -> tuple[dict[str, Any], list[str], Brand | None]:
        """简称 / 季节 / 品牌 / 图片：不合法丢弃并提示（行不失败）；品牌按启用品牌匹配。

        返回（处理后的 parsed、提示、匹配到的品牌）。被丢弃的项算「文件没给值」，不参与比较。
        提示文案不含受保护字段的值。
        """
        out = dict(parsed)
        warnings: list[str] = []
        short = out.get("goods_short_name")
        if short and len(short) > GOODS_SHORT_NAME_MAX_LEN:
            out["goods_short_name"] = None
            warnings.append(f"商品简称超过 {GOODS_SHORT_NAME_MAX_LEN} 字，未写入")
        season = out.get("season")
        if season and len(season) > _SEASON_MAX_LEN:
            out["season"] = None
            warnings.append(f"季节超过 {_SEASON_MAX_LEN} 字，未写入")
        raw_url = out.get("external_image_url")
        if raw_url:
            url = normalize_external_image_url(raw_url)
            out["external_image_url"] = url
            if url is None:
                warnings.append("图片链接不是 http/https 地址或超过 1024 字符，未保存")
        brand: Brand | None = None
        brand_text = out.get("brand_code")
        if brand_text:
            if len(brand_text) > _BRAND_MAX_LEN:
                warnings.append(f"品牌超过 {_BRAND_MAX_LEN} 字，未写入")
            else:
                brand = await self._match_brand(session, tenant_id, brand_text)
                if brand is None:
                    warnings.append(f"品牌「{brand_text}」不在品牌字典里，未写入")
        return out, warnings, brand

    async def _match_brand(self, session: AsyncSession, tenant_id: UUID, text: str) -> Brand | None:
        """按启用品牌的 brand_code 或 brand_name 精确匹配；两者命中不同品牌时取 brand_code 的。"""
        stmt = (
            select(Brand)
            .where(
                Brand.tenant_id == tenant_id,
                Brand.is_active.is_(True),
                or_(Brand.brand_code == text, Brand.brand_name == text),
            )
            .order_by(Brand.brand_code)
        )
        brands = list((await session.execute(stmt)).scalars().all())
        by_code = next((b for b in brands if b.brand_code == text), None)
        return by_code or (brands[0] if brands else None)

    # ----------------------- 比较字段（8a-6）----------------------- #

    def compare_specs(self) -> tuple[FieldSpec, ...]:
        """款式 / SKU / 商品三个对象的比较字段（款号、SKU 编码是判重键，商品名称仅新建时写入）。"""
        return (*_STYLE.specs, *_SKU.specs, *_GOODS.specs)

    def compare_field_names(self) -> frozenset[str]:
        """冲突按字段筛选时认的字段名。"""
        return frozenset(spec.name for spec in self.compare_specs())

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
        """按 ``rule_for(source)`` 处理一行（设计 §5.2）：第 0 步 → 款式 → SKU → 商品层。

        行类别按「冲突 > 新增 > 已覆盖 > 补空 > 重复已跳过」合成；``resource_id`` 记 SKU id。
        键冲突与「商品名称仅新建时写入」在三种策略下都一样（J55）。不自行 commit。
        """
        rule = rule_for(ctx.source)
        parsed, warnings, brand = await self.sanitize_optional(
            parsed, session=session, tenant_id=ctx.tenant_id
        )
        row = _Row(self, session, ctx, rule, parsed, brand, warnings)

        # 0) 先按两个键查（不加锁，只用来判断）
        style = await StyleRepository(session).get_by_code(parsed["style_code"])
        sku = await SkuRepository(session).get_by_code(parsed["sku_code"])
        if style is None and sku is not None:
            return await row.sku_owned_by_other_style(sku)

        # 1) 款式
        if style is None:
            style = await row.create_style()
        else:
            await row.existing(
                _STYLE,
                style.id,
                {"external_image_url": parsed.get("external_image_url")},
                key=style.style_code,
                label=_style_label(style),
                gone=f"款式 {style.style_code} 在导入期间被删除，请重试",
            )

        # 2) SKU
        if sku is None:
            sku = await row.create_sku(style)
        elif rule.policy is DuplicatePolicy.KEEP and sku.style_id == style.id:
            row.kinds.append(RowKind.SKIPPED)
        else:
            await row.existing_sku(sku, style)

        # 3) 商品层
        await row.goods_layer(style)
        return RowOutcome(
            resource_id=sku.id,
            kind=merge_kinds(row.kinds),
            warnings=row.warnings,
            filled=row.filled,
        )


class _Row:
    """一行的处理状态：行类别、提示、补空明细，以及处理已有 / 新建对象的步骤。"""

    def __init__(
        self,
        adapter: StyleSkuImportAdapter,
        session: AsyncSession,
        ctx: ImportRowContext,
        rule: DuplicateRule,
        parsed: dict[str, Any],
        brand: Brand | None,
        warnings: list[str],
    ) -> None:
        self.adapter = adapter
        self.session = session
        self.ctx = ctx
        self.rule = rule
        self.parsed = parsed
        self.brand = brand
        self.warnings = warnings
        self.kinds: list[RowKind] = []
        self.filled: list[FilledRecord] = []
        self.recorder = ConflictRecorder(session, ctx)

    # -- 第 0 步：款式要新建，而 SKU 编码已属于别的款式 --------------------------

    async def sku_owned_by_other_style(self, sku: Sku) -> RowOutcome:
        """不建款式、不建单品商品，只对这个 SKU 记键冲突（固定顺序：锁冲突 → 锁 SKU → record）。

        所属款式 X 按 ``sku.style_id`` 取（含已删除：删款式不删停用的 SKU）。X 已删除且款号与
        文件里的款式编码相同（不区分大小写）时，文案改为提示先恢复款式 X（NIT 2）。
        """
        file_code = str(self.parsed["style_code"])
        c = await self.recorder.lock_pending(_SKU.object_type, sku.id)
        locked = await _SKU.load_for_update(self.session, sku.id)
        if locked is None:
            raise RowValidationError(f"SKU {sku.sku_code} 在导入期间被删除，请重试")
        owner = await StyleRepository(self.session).get_by_id(locked.style_id, include_deleted=True)
        owner_code = owner.style_code if owner is not None else "（未知）"
        deleted = owner is not None and owner.is_deleted
        deleted_same = deleted and owner_code.lower() == file_code.lower()
        if deleted_same:
            message = (
                f"款式 {owner_code} 已删除，但其下仍有 SKU {locked.sku_code}；"
                f"如需继续使用，请先恢复款式 {owner_code}（联系管理员）"
            )
            warning = message
        else:
            mark = "（已删除）" if deleted else ""
            message = (
                f"SKU 编码 {locked.sku_code} 已属于款式 {owner_code}{mark}，文件里是款式 "
                f"{file_code}（系统里还没有）；本行未建款式与单品商品"
            )
            warning = f"SKU 编码已属于款式 {owner_code}，本行未建款式与单品商品"
        self.warnings += await self.record_key(
            c, _SKU, locked, key=locked.sku_code, label=_sku_label(locked), message=message
        )
        self.warnings.append(warning)
        return RowOutcome(resource_id=locked.id, kind=RowKind.CONFLICT, warnings=self.warnings)

    # -- 新建对象（不加锁；写入后登记文件给的值，§4.2.1） ---------------------------

    async def create_style(self) -> Style:
        """新建款式：款名 = 商品名称、类目为空、外部图片链接；不写简称 / 季节 / 品牌（J8）。"""
        p = self.parsed
        style = Style(
            tenant_id=self.ctx.tenant_id,
            style_code=p["style_code"],
            style_name=p["style_name"],
            category=None,  # 类目下线（8a-3）：导入不再写类目，也不写「未分类」
            external_image_url=p.get("external_image_url"),
            owner_id=self.ctx.actor_id,
            design_status="大货",
        )
        self.session.add(style)
        await self.session.flush()  # 拿 style.id；并发撞 uq_style_code → 行失败（重试走比较）
        screen_batch_seen(
            self.ctx,
            _STYLE.object_type,
            style.id,
            _STYLE.specs,
            {"external_image_url": style.external_image_url},
        )
        self.kinds.append(RowKind.INSERTED)
        return style

    def sku_incoming(self) -> dict[str, Any]:
        """文件给了值的 SKU 比较字段（没给的不出现）。"""
        return {
            spec.name: self.parsed[spec.name]
            for spec in _SKU.specs
            if self.parsed.get(spec.name) is not None
        }

    async def create_sku(self, style: Style) -> Sku:
        """ORM 新建 SKU（不走 upsert_atomic；与现状一样不做 validate_sku_sourcing_price）。"""
        incoming = self.sku_incoming()
        money = {
            name: check_money(incoming[name]) if name in incoming else None
            for name in _MONEY_FIELDS
        }
        sku = Sku(
            tenant_id=self.ctx.tenant_id,
            style_id=style.id,
            sku_code=self.parsed["sku_code"],
            color=self.parsed["color"],
            size=self.parsed["size"],
            sourcing_type=self.parsed.get("sourcing_type") or "自产",
            **money,
        )
        self.session.add(sku)
        await self.session.flush()  # 并发撞 uq_sku_code → IntegrityError → 行失败，重试即走比较
        screen_batch_seen(self.ctx, _SKU.object_type, sku.id, _SKU.specs, incoming)
        self.kinds.append(RowKind.INSERTED)
        return sku

    def goods_incoming(self) -> dict[str, Any]:
        values = {
            "short_name": self.parsed.get("goods_short_name"),
            "brand_id": self.brand.id if self.brand is not None else None,
            "season": self.parsed.get("season"),
        }
        return {k: v for k, v in values.items() if v is not None}

    # -- 已有对象（固定顺序：锁冲突 → 加锁重读 → 锁内比较 → 写入 → touch / record） -------

    async def existing(
        self,
        applier: ConflictApplier,
        object_id: UUID,
        incoming: Mapping[str, Any],
        *,
        key: str,
        label: str,
        gone: str,
        is_writable: Callable[[Any], bool] | None = None,
    ) -> None:
        """比较 / 补空 / 覆盖一个已有对象。文件没给它任何比较字段 → 不处理；KEEP → 不加锁、跳过。"""
        given = {k: v for k, v in incoming.items() if v is not None}
        if not given:
            return
        if self.rule.policy is DuplicatePolicy.KEEP:
            self.kinds.append(RowKind.SKIPPED)
            return
        c = await self.recorder.lock_pending(applier.object_type, object_id)
        obj = await applier.load_for_update(self.session, object_id)
        if obj is None or (is_writable is not None and not is_writable(obj)):
            raise RowValidationError(gone)
        await self.compare_and_write(applier, obj, c, given, key=key, label=label)

    async def existing_sku(self, sku: Sku, style: Style) -> None:
        """已有 SKU：锁内看它属于哪个款式。属于别的款式 → 键冲突（三种策略都一样，不改 SKU）。"""
        c = await self.recorder.lock_pending(_SKU.object_type, sku.id)
        locked = await _SKU.load_for_update(self.session, sku.id)
        if locked is None:
            raise RowValidationError(f"SKU {sku.sku_code} 在导入期间被删除，请重试")
        if locked.style_id != style.id:
            owner = await StyleRepository(self.session).get_by_id(
                locked.style_id, include_deleted=True
            )
            owner_code = owner.style_code if owner is not None else "（未知）"
            message = (
                f"SKU 编码 {locked.sku_code} 已属于款式 {owner_code}，文件里是款式 "
                f"{style.style_code}；SKU 未改"
            )
            self.warnings += await self.record_key(
                c, _SKU, locked, key=locked.sku_code, label=_sku_label(locked), message=message
            )
            self.warnings.append(f"SKU 编码已属于款式 {owner_code}，本行未改 SKU")
            self.kinds.append(RowKind.CONFLICT)
            return
        if self.rule.policy is DuplicatePolicy.KEEP:
            self.kinds.append(RowKind.SKIPPED)
            return
        await self.compare_and_write(
            _SKU, locked, c, self.sku_incoming(), key=locked.sku_code, label=_sku_label(locked)
        )

    async def compare_and_write(
        self,
        applier: ConflictApplier,
        obj: Any,
        c: Any,
        incoming: Mapping[str, Any],
        *,
        key: str,
        label: str,
    ) -> None:
        """锁内：screen_batch_seen → diff_fields → 补空 / 覆盖 → touch / record。"""
        specs = applier.specs
        object_type = applier.object_type
        current = applier.current_values(obj, [spec.name for spec in specs])
        screened, warnings = screen_batch_seen(self.ctx, object_type, obj.id, specs, incoming)
        self.warnings += warnings
        sys_displays = await self.current_displays(applier, obj)
        displays = None
        if sys_displays is not None and "brand_id" in screened:
            file_name = self.brand.brand_name if self.brand is not None else None
            displays = {"brand_id": (sys_displays.get("brand_id"), file_name)}
        diff = diff_fields(
            specs, current, screened, fill_empty=self.rule.fill_empty, displays=displays
        )
        screened_norm = {
            spec.name: normalize(spec.kind, screened[spec.name])
            for spec in specs
            if spec.name in screened
        }

        if self.rule.policy is DuplicatePolicy.OVERWRITE:
            to_write = {**diff.fills, **{d.field: d.file for d in diff.conflicts}}
            changes = await self.write(applier, obj, to_write, via="import_overwrite")
            self.warnings += await self.recorder.touch(
                c, object_type, obj.id, compared=diff.compared, incoming=screened_norm
            )
            self.kinds.append(RowKind.UPDATED if changes else RowKind.SKIPPED)
            return

        # COMPARE：补空照做，冲突只含两边都有值且不同的字段
        changes = await self.write(applier, obj, dict(diff.fills), via="import_fill")
        if changes:
            self.filled.append(FilledRecord(object_type, label, list(changes)))
            self.kinds.append(RowKind.FILLED)
        else:
            self.kinds.append(RowKind.SKIPPED)
        if diff.conflicts:
            self.warnings += await self.recorder.record(
                c,
                object_type,
                obj.id,
                key,
                label,
                kind="fields",
                diffs=diff.conflicts,
                compared=diff.compared,
                current=current,
                message=None,
                current_displays=sys_displays,
            )
            self.kinds.append(RowKind.CONFLICT)
        else:
            self.warnings += await self.recorder.touch(
                c, object_type, obj.id, compared=diff.compared, incoming=screened_norm
            )

    async def record_key(
        self, c: Any, applier: ConflictApplier, obj: Any, *, key: str, label: str, message: str
    ) -> list[str]:
        """键冲突：不比较字段（compared 为空），current 用锁内读到的全部比较字段。"""
        return await self.recorder.record(
            c,
            applier.object_type,
            obj.id,
            key,
            label,
            kind="key",
            diffs=[],
            compared=(),
            current=applier.current_values(obj, [spec.name for spec in applier.specs]),
            message=message,
            current_displays=await self.current_displays(applier, obj),
        )

    async def current_displays(
        self, applier: ConflictApplier, obj: Any
    ) -> dict[str, str | None] | None:
        """REF 字段当前值的显示名：只有商品的品牌。"""
        if applier.object_type != _GOODS.object_type:
            return None
        brand_id = getattr(obj, "brand_id", None)
        brand = await self.session.get(Brand, brand_id) if brand_id is not None else None
        return {"brand_id": brand.brand_name if brand is not None else None}

    async def write(
        self, applier: ConflictApplier, obj: Any, to_write: Mapping[str, Any], *, via: str
    ) -> dict[str, tuple[Any, Any]]:
        """经 applier 校验并写入；不合法的字段丢弃、加提示、撤回 batch_seen 登记（行不失败）。"""
        if not to_write:
            return {}
        values: dict[str, Any] = {}
        for name, value in to_write.items():
            try:
                values[name] = applier.check(name, value)
            except APPLIER_VALUE_ERRORS as exc:
                self.drop(applier, obj, name, exc)
        try:
            await applier.check_refs(self.session, values)
        except APPLIER_VALUE_ERRORS as exc:
            bad = exc.field if isinstance(exc, ApplierValueError) else None
            for name in [bad] if bad in values else list(values):
                assert name is not None
                values.pop(name)
                self.drop(applier, obj, name, exc)
        changes = await applier.apply(self.session, obj, values)
        await write_object_audit(
            self.session,
            applier,
            obj.id,
            changes,
            via=via,
            batch_id=self.ctx.batch_id,
            user_id=self.ctx.actor_id,
            actor_type="worker",
            row_number=self.ctx.row_number,
        )
        return changes

    def drop(self, applier: ConflictApplier, obj: Any, name: str, exc: BaseException) -> None:
        label = next((s.label for s in applier.specs if s.name == name), name)
        self.warnings.append(f"{label} 的值不合法（{invalid_reason(exc)}），未写入")
        self.ctx.batch_seen.staged.pop((applier.object_type, obj.id, name), None)

    # -- 商品层（§5.3） ------------------------------------------------------------

    async def goods_layer(self, style: Style) -> None:
        """按决策写入已有单品、记键冲突或新建单品商品；套装永远不改。"""
        gctx = await load_goods_context(
            self.session, style.id, style.style_code, tenant_id=self.ctx.tenant_id
        )
        decision = decide_goods_target(gctx.code_goods, gctx.memberships)
        self.warnings.extend(decision.warnings)
        target = decision.target
        if decision.action is GoodsAction.CREATE:
            goods = await create_single_goods(
                self.session, self.ctx, style, self.parsed, self.brand
            )
            screen_batch_seen(
                self.ctx, _GOODS.object_type, goods.id, _GOODS.specs, self.goods_incoming()
            )
            self.kinds.append(RowKind.INSERTED)
        elif decision.action is GoodsAction.WRITE and target is not None:
            await self.existing(
                _GOODS,
                target.id,
                self.goods_incoming(),
                key=target.display_name,
                label=target.display_name,
                gone=f"商品「{target.display_name}」在导入期间被改动，请重试",
                is_writable=lambda g: not g.is_suit,
            )
        elif decision.action is GoodsAction.KEY_CONFLICT and target is not None:
            c = await self.recorder.lock_pending(_GOODS.object_type, target.id)
            obj = await _GOODS.load_for_update(self.session, target.id, include_deleted=True)
            if obj is None:
                raise RowValidationError(f"商品「{target.display_name}」在导入期间被删除，请重试")
            label = goods_display_name(obj.goods_title, obj.short_name)
            self.warnings += await self.record_key(
                c,
                _GOODS,
                obj,
                key=label,
                label=label,
                message=decision.conflict_message or "款号对应的商品编码已被占用",
            )
            self.kinds.append(RowKind.CONFLICT)


def register() -> None:
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(StyleSkuImportAdapter())


__all__ = ["MAPPING_TARGETS", "StyleSkuImportAdapter", "register"]
