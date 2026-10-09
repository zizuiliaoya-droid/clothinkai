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
- platform 空 → 缺省 "小红书"
- mapping=None 回退内置默认映射（domain-entities §4）

8b（设计 §6.2、§6.5、§6.6）：
- 判重键（平台, 账号）；表头「账号」（别名小红书ID / 小红书号）等，主列没值再试别名；加「主页链接」与
  可选的「网页ID」（N2）；平台必须是 ``Platform`` 枚举值（运行时取），账号与博主接口同一正则（N7）
- 粉丝数 / 报价按 ``cn_numbers.parse_cn_number`` 解析（千分位、w / 万 / 亿）
- R2：COMPARE 里两边不同的字段，``always_overwrite()`` 声明的以文件为准覆盖（审计 via=import_overwrite），
  其余照旧进冲突
- 类目标签只收启用字典里的，字典外与系统标签词丢掉、整批提示一次；质量标签（系统标签）不读不写，
  文件里有值整批提示一次
- ``_write_snapshot``：抖音统计快照的钩子（``huitun_douyin`` 子类实现），这里空实现
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.enums import Platform
from app.modules.blogger.models import Blogger
from app.modules.blogger.repository import BloggerRepository
from app.modules.blogger.tag_config import SYSTEM_TAGS
from app.modules.blogger.tag_dict import BloggerTagDictService
from app.modules.importer.cn_numbers import parse_cn_number
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
    BloggerApplier,
    invalid_reason,
    write_object_audit,
)
from app.modules.importer.conflicts import ConflictRecorder
from app.modules.importer.duplicate_rules import (
    SWITCHABLE_POLICIES,
    DuplicatePolicy,
    always_overwrite,
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

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

_OBJECT_TYPE = "blogger"
_APPLIER = BloggerApplier()

# 标签分隔符（中英文分号/逗号）
_TAG_SEP = re.compile(r"[;；,，]")

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段；主列没值再试 aliases，8b §6.2）。
# 「账号」历史原因落在 xiaohongshu_id 列（8b K2，语义 = 平台账号）。模版去掉了「质量标签」（系统标签只读）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {
        "source_col": "账号",
        "target_field": "xiaohongshu_id",
        "type": "str",
        "aliases": ["小红书ID", "小红书号"],
    },
    {"source_col": "昵称", "target_field": "nickname", "type": "str", "aliases": ["小红书昵称"]},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "微信", "target_field": "wechat", "type": "str", "aliases": ["微信号"]},
    {"source_col": "手机号", "target_field": "phone", "type": "str"},
    {
        "source_col": "粉丝数",
        "target_field": "follower_count",
        "type": "int",
        "aliases": ["粉丝量"],
    },
    {"source_col": "博主类型", "target_field": "blogger_type", "type": "str"},
    {"source_col": "性别投放", "target_field": "gender_target", "type": "str"},
    {
        "source_col": "类目标签",
        "target_field": "category_tags",
        "type": "list",
        "aliases": ["标签"],
    },
    {"source_col": "报价", "target_field": "quote", "type": "decimal"},
    {"source_col": "合作历史", "target_field": "cooperation_history", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
    {"source_col": "主页链接", "target_field": "homepage_url", "type": "str"},
    {"source_col": "网页ID", "target_field": "web_id", "type": "str"},
]

# 旧模版的「质量标签」列：不读值，只用来判断要不要给整批提示（D5，r2 N17）
_QUALITY_TAGS_COLUMN = "质量标签"
_QUALITY_TAGS_FIELD = "quality_tags"

_DEFAULT_PLATFORM = Platform.XIAOHONGSHU.value
# 与博主接口 schemas._ACCOUNT_PATTERN 同一规则（N7：导进来的账号在编辑时不会 422）
_ACCOUNT_RE = re.compile(r"[A-Za-z0-9_.\-]+")
ACCOUNT_PATTERN_ERROR = "账号只能包含字母、数字、_ . -"
_INT32_LIMIT = 2**31
# 原文带 w / 万 / 亿 单位：解析出的小数四舍五入取整（「1.25w」≈ 12500）
_UNIT_SUFFIX = re.compile(r"[wW万亿]\s*$")

# 整批只提示一次的提示（BatchSeen.first_notice 的键与文案）
UNKNOWN_TAGS_NOTICE_KEY = "blogger.unknown_tags"
UNKNOWN_TAGS_NOTICE = "本批有类目标签不在标签字典、未写入，汇总见博主页「标签字典 → 导入缺的标签」"
QUALITY_TAGS_NOTICE_KEY = "blogger.quality_tags_ignored"
QUALITY_TAGS_NOTICE = "质量标签是系统标签，由重算自动计算，导入不写入（整批只提示一次）"

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("xiaohongshu_id", "账号不能为空"),
    ("nickname", "昵称不能为空"),
)
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("xiaohongshu_id", 64),
    ("nickname", 128),
    ("wechat", 64),
    ("phone", 32),
    ("platform", 16),
    ("blogger_type", 16),
    ("gender_target", 16),
    ("web_id", 64),
)


def _split_tags(raw: Any) -> list[str]:
    """分隔字符串（;；,，）→ 拆分 + strip + 去空、去占位符 → list。空 → []。"""
    if raw is None or str(raw).strip() == "":
        return []
    return [t.strip() for t in _TAG_SEP.split(str(raw)) if not is_placeholder(t)]


def _to_int(raw: Any) -> int | str | None:
    """粉丝数（8b §6.4）：千分位、w / 万 / 亿、占位符。整数 → int；带单位的小数 ROUND_HALF_UP 取整；
    不带单位的小数与解析不了的值保留原串供 validate 报错。空 → None。
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = parse_cn_number(raw)
    except ValueError:
        return str(raw)
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    if isinstance(raw, str) and _UNIT_SUFFIX.search(raw):
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return str(raw)


def _to_decimal(raw: Any) -> Decimal | str | None:
    """报价（8b §6.4）：千分位、w / 万 / 亿，Decimal（禁 float）。非法值保留原串。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return parse_cn_number(raw)
    except ValueError:
        return str(raw)


def _cell(row: Mapping[str, Any], col: Mapping[str, Any]) -> Any:
    """主列的值；主列没值（空 / 占位符）再按顺序试别名。都没有 → None。"""
    raw = row.get(col["source_col"])
    if not is_placeholder(raw):
        return raw
    for alias in col.get("aliases") or []:
        value = row.get(alias)
        if not is_placeholder(value):
            return value
    return None


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
    # 必填字段与为空时的行失败原因（灰豚抖音子类换成自己的文案）
    _REQUIRED_ERRORS: ClassVar[tuple[tuple[str, str], ...]] = _REQUIRED

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
        # 8b D5（r2 N17）：质量标签只由重算写。旧模版的「质量标签」列或自定义映射到 quality_tags 的列
        # 有值 → 只记 quality_tags_present（不在 specs 里，只给整批提示用），不取值
        if _split_tags(row.get(_QUALITY_TAGS_COLUMN)):
            parsed["quality_tags_present"] = True
        for col in columns:
            # 占位符（-、--、—、——）与空格子一样当没给值（§5.5，N17）：新建不写进库、已有不补空也不冲突
            raw = _cell(row, col)
            target = col["target_field"]
            if target == _QUALITY_TAGS_FIELD:
                if _split_tags(raw):
                    parsed["quality_tags_present"] = True
                continue
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
        for field, message in self._REQUIRED_ERRORS:
            if not parsed.get(field):
                errs.append(message)
        account = parsed.get("xiaohongshu_id")
        if isinstance(account, str) and account and not _ACCOUNT_RE.fullmatch(account):
            errs.append(ACCOUNT_PATTERN_ERROR)
        platform = parsed.get("platform")
        allowed = [p.value for p in Platform]  # 运行时取枚举（流程线加的平台自动生效）
        if platform and platform not in allowed:
            errs.append(f"平台必须为 {'/'.join(allowed)} 之一")
        follower = parsed.get("follower_count")
        if follower is not None and (
            isinstance(follower, bool) or not isinstance(follower, int) or follower < 0
        ):
            errs.append("粉丝数必须为非负整数")
        elif follower is not None and follower >= _INT32_LIMIT:
            errs.append("粉丝数必须为 0 到 2147483647 之间的整数")
        quote = parsed.get("quote")
        if quote is not None and not isinstance(quote, Decimal):
            errs.append("报价必须为非负数字")
        elif quote is not None:
            try:
                check_money(quote)
            except ValueError:
                errs.append(f"报价{MONEY_REASON}")
        for field, max_len in _MAX_LEN:
            value = parsed.get(field)
            if value and isinstance(value, str) and len(value) > max_len:
                errs.append(f"{field} 超过长度上限 {max_len}")
        return errs

    # ----------------------- 比较字段（8a-6）----------------------- #

    # 8b §6.2：平台（判重键）与质量标签（系统标签）只为读旧冲突留在 applier.specs，不比较；
    # 报价备注只有灰豚抖音来源有（子类改这两个集合）
    _NOT_COMPARED: ClassVar[frozenset[str]] = frozenset({"platform", "quality_tags", "quote_note"})
    _NOT_FILTERABLE: ClassVar[frozenset[str]] = frozenset({"quote_note"})

    def compare_specs(self) -> tuple[FieldSpec, ...]:
        """比较用的字段（§5.5 的表；账号是判重键，不比较）。"""
        return tuple(s for s in _APPLIER.specs if s.name not in self._NOT_COMPARED)

    def compare_field_names(self) -> frozenset[str]:
        """冲突按字段筛选时认的字段名（含平台、质量标签：旧冲突还能筛出来批量保留）。"""
        return frozenset(s.name for s in _APPLIER.specs if s.name not in self._NOT_FILTERABLE)

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

        - 先 ``_prepare``：丢掉不合法的主页链接、字典外的类目标签，给整批提示（8b §6.6）
        - 没有该（平台, 账号）的博主（软删的不算）→ ORM 新建（平台空缺省「小红书」、标签空为 []、
          质量标签 []、博主类型原样），写入后登记比较字段
        - 已有：KEEP 不加锁直接「重复已跳过」；COMPARE / OVERWRITE 先锁冲突、再加锁重读博主，锁内
          ``screen_batch_seen`` → ``diff_fields``；补空 / 覆盖经 applier 写入并留痕；COMPARE 里
          ``always_overwrite()`` 声明的字段以文件为准覆盖（R2），其余两边不同的 → record，否则 touch。
          补空 / 覆盖 / 裁决都**不重算**博主类型
        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        """
        repo = BloggerRepository(session)
        xhs_id = parsed["xiaohongshu_id"]
        warnings: list[str] = []
        parsed = await self._prepare(parsed, session=session, ctx=ctx, warnings=warnings)
        specs = self.compare_specs()
        incoming = {spec.name: parsed[spec.name] for spec in specs if spec.name in parsed}

        # 8b：按（平台, 账号）判重；平台空缺省小红书（与 _insert 同口径）
        existing = await repo.get_by_account(parsed.get("platform") or _DEFAULT_PLATFORM, xhs_id)
        if existing is None:
            return await self._insert(parsed, session=session, ctx=ctx, warnings=warnings)
        reason = self._identity_mismatch(existing, parsed, ctx)
        if reason is not None:  # 可能是不同的人：整行不导（不补空、不覆盖、不记冲突）
            warnings.append(reason)
            return RowOutcome(resource_id=existing.id, kind=RowKind.SKIPPED, warnings=warnings)

        rule = rule_for(ctx.source)
        if rule.policy is DuplicatePolicy.KEEP:
            return RowOutcome(resource_id=existing.id, kind=RowKind.SKIPPED, warnings=warnings)

        recorder = ConflictRecorder(session, ctx)
        conflict = await recorder.lock_pending(_OBJECT_TYPE, existing.id)
        blogger = await _APPLIER.load_for_update(session, existing.id)
        if blogger is None:
            raise RowValidationError(f"博主 {xhs_id} 在导入期间被删除，请重试")

        names = [spec.name for spec in specs]
        current = _APPLIER.current_values(blogger, names)
        screened, seen_warnings = screen_batch_seen(ctx, _OBJECT_TYPE, blogger.id, specs, incoming)
        warnings += seen_warnings
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
            snapshot = await self._write_snapshot(
                blogger, parsed, session=session, ctx=ctx, policy=rule.policy, warnings=warnings
            )
            warnings += await recorder.touch(
                conflict, _OBJECT_TYPE, blogger.id, compared=diff.compared, incoming=screened_norm
            )
            kind = RowKind.UPDATED if changes or snapshot else RowKind.SKIPPED
            return RowOutcome(resource_id=blogger.id, kind=kind, warnings=warnings)

        # COMPARE：补空照做；两边都有值且不同的，R2 声明的字段以文件为准覆盖，其余进冲突
        changes = await self._write(
            blogger,
            dict(diff.fills),
            session=session,
            ctx=ctx,
            via="import_fill",
            warnings=warnings,
        )
        overwrite = {
            d.field: d.file for d in diff.conflicts if always_overwrite(_OBJECT_TYPE, d.field)
        }
        conflicts = [d for d in diff.conflicts if d.field not in overwrite]
        overwritten = await self._write(
            blogger,
            overwrite,
            session=session,
            ctx=ctx,
            via="import_overwrite",
            warnings=warnings,
        )
        snapshot = await self._write_snapshot(
            blogger, parsed, session=session, ctx=ctx, policy=rule.policy, warnings=warnings
        )
        filled = [FilledRecord(_OBJECT_TYPE, label, list(changes))] if changes else []
        kinds = [RowKind.SKIPPED]
        if changes:
            kinds.append(RowKind.FILLED)
        if overwritten or snapshot:
            kinds.append(RowKind.UPDATED)
        if conflicts:
            warnings += await recorder.record(
                conflict,
                _OBJECT_TYPE,
                blogger.id,
                f"{blogger.platform}·{xhs_id}",
                label,
                kind="fields",
                diffs=conflicts,
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

    async def _prepare(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
        warnings: list[str],
    ) -> dict[str, Any]:
        """比较 / 新建之前的取舍（8b §6.6、§9），返回新的 parsed：

        - 主页链接不合法 → 丢这一格、行提示（行照常）
        - 类目标签只留启用字典里的，系统标签词与字典外的丢掉；整批只提示一次，汇总见 ``/missing``
        - 文件给了质量标签 → 不写，整批只提示一次
        """
        prepared = dict(parsed)
        url = prepared.get("homepage_url")
        if url is not None:
            try:
                prepared["homepage_url"] = _APPLIER.check("homepage_url", url)
            except APPLIER_VALUE_ERRORS as exc:
                warnings.append(f"主页链接 的值不合法（{invalid_reason(exc)}），未写入")
                prepared["homepage_url"] = None
        tags = prepared.get("category_tags")
        if tags:
            allowed = await BloggerTagDictService(session).active_values(ctx.tenant_id, tags)
            kept = [t for t in tags if t in allowed and t not in SYSTEM_TAGS]
            if len(kept) < len(tags) and ctx.batch_seen.first_notice(UNKNOWN_TAGS_NOTICE_KEY):
                warnings.append(UNKNOWN_TAGS_NOTICE)
            prepared["category_tags"] = kept or None
        if prepared.pop("quality_tags_present", False) and ctx.batch_seen.first_notice(
            QUALITY_TAGS_NOTICE_KEY
        ):
            warnings.append(QUALITY_TAGS_NOTICE)
        return prepared

    def _identity_mismatch(
        self, existing: Blogger, parsed: dict[str, Any], ctx: ImportRowContext
    ) -> str | None:
        """命中已有 / 本批刚建的博主后、加锁之前：返回提示 = 本行整行不导。手工模版不判，返回 None。"""
        return None

    def _insert_extra(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """新建时另写的列（灰豚抖音的报价备注）；手工模版没有。"""
        return {}

    async def _write_snapshot(
        self,
        blogger: Blogger,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
        policy: DuplicatePolicy | None,
        warnings: list[str],
    ) -> bool:
        """平台统计快照（``platform_metrics``，8b §6.5）的钩子：``policy=None`` 是本行新建的博主。

        返回是否写入。手工模版没有快照，空实现；灰豚抖音来源覆盖它。KEEP 分支不调。
        """
        return False

    async def _insert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
        warnings: list[str],
    ) -> RowOutcome:
        values: dict[str, Any] = {
            "xiaohongshu_id": parsed["xiaohongshu_id"],
            "nickname": parsed["nickname"],
            "platform": parsed.get("platform") or _DEFAULT_PLATFORM,
            "wechat": parsed.get("wechat"),
            "phone": parsed.get("phone"),
            "follower_count": parsed.get("follower_count"),
            "blogger_type": parsed.get("blogger_type"),
            "gender_target": parsed.get("gender_target"),
            "category_tags": parsed.get("category_tags") or [],
            "quality_tags": [],  # 系统标签只由重算写（8b D5）
            "quote": parsed.get("quote"),
            "cooperation_history": parsed.get("cooperation_history"),
            "remark": parsed.get("remark"),
            "web_id": parsed.get("web_id"),
            "homepage_url": parsed.get("homepage_url"),
            **self._insert_extra(parsed),
        }
        blogger = Blogger(tenant_id=ctx.tenant_id, **values)
        session.add(blogger)
        await session.flush()
        # 新对象的 id 不可能已登记过：这一步只为登记，同批后面的行以本行写入的值为准
        screen_batch_seen(ctx, _OBJECT_TYPE, blogger.id, self.compare_specs(), values)
        await self._write_snapshot(
            blogger, parsed, session=session, ctx=ctx, policy=None, warnings=warnings
        )
        return RowOutcome(resource_id=blogger.id, kind=RowKind.INSERTED, warnings=warnings)

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
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。

    灰豚抖音博主库（``huitun_douyin``，8b §6.3）一并注册，不改 ``main.py``。
    """
    from app.modules.importer.adapters.blogger_douyin import HuitunDouyinImportAdapter

    ImportAdapterRegistry.register(BloggerImportAdapter())
    ImportAdapterRegistry.register(HuitunDouyinImportAdapter())


__all__ = ["BloggerImportAdapter", "register"]
