"""8b 灰豚抖音博主库导入（来源 ``huitun_douyin``，设计 §6.3、§6.5；补充一 D1、补充二 D3 / R2）。

子类化 ``manual_blogger`` 的 adapter：判重（平台, 账号）/ 补空 / 冲突 / 同批以第一行为准 / 裁决全复用，
只换「读文件」这一层：

- 版式固定（``file_layout``）：读「抖音博主库」sheet，「博主ID」那行是表头，上面一行是分组，重名列改成
  「分组·列名」；找不到 → 批次失败、提示传原文件。「7天视频详情」sheet 不导
- 平台固定抖音；不读租户自定义映射（版式固定）；联系人不导（D4：存的是我方 PR 工作机）
- 博主ID → 账号（``xiaohongshu_id`` 列，K2）；为空 → 行失败并写清原因（D1③）
- 报价：原文一律写报价备注（与报价同一条字段权限），能解析成单个非负数的同时写报价（D3，``_douyin_quote``）
- 第 7 ~ 29 列（含粉丝总量）进 ``platform_metrics``：``raw`` 留原文、``values`` 收能解析的数值，只展示
  不计算；以文件为准覆盖（R2，``duplicate_rules.ALWAYS_OVERWRITE_FIELDS``），写入留审计（只记「变了」）
- 同批「博主ID 相同、网页ID 不同」→ 整行不导并提示（D1②）；跨批网页ID 不同 → 普通字段冲突（D1①）
- 失败明细的遮挡靠自己的 ``builtin_columns()``（r1 N3）；迁移 059 下行从原文里去掉的列与它一致（r2 M4）
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.blogger.enums import Platform
from app.modules.blogger.models import Blogger
from app.modules.importer.adapters.blogger import (
    APPLIER,
    OBJECT_TYPE,
    BloggerImportAdapter,
    to_int,
)
from app.modules.importer.cn_numbers import parse_cn_number
from app.modules.importer.compare import ValueKind, check_money, is_placeholder, normalize
from app.modules.importer.duplicate_rules import DuplicatePolicy, always_overwrite
from app.modules.importer.file_layout import XlsxLayout
from app.modules.importer.outcome import ImportRowContext

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

SOURCE = "huitun_douyin"
DOUYIN_SHEET = "抖音博主库"

# 灰豚导出的列名（第 2 行表头）
COL_CONTACT = "联系人"  # 不导（D4）
COL_NICKNAME = "抖音博主"
COL_ACCOUNT = "博主ID"
COL_WEB_ID = "网页ID"
COL_WECHAT = "微信号"
COL_QUOTE = "报价"
COL_FOLLOWER = "粉丝总量"

# 第 7 ~ 29 列：进 platform_metrics（重名列带分组前缀）。前端展示清单与它逐字一致
# （frontend/src/features/blogger/douyinMetrics.snapshot.json，tests/unit/test_douyin_constants.py 比对）
DOUYIN_METRIC_COLUMNS: tuple[str, ...] = (
    "灰豚指数",
    COL_FOLLOWER,
    "赞粉比",
    "新增粉丝",
    "新增内容",
    "预估曝光",
    "新增点赞",
    "新增评论",
    "性别分布",
    "年龄分布",
    "视频数（不用抓取）",
    "30天视频分析·点赞",
    "30天视频分析·评论",
    "30天视频分析·分享",
    "30天视频分析·收藏",
    "30天视频分析·互动量",
    "视频数",
    "7天视频分析·点赞",
    "7天视频分析·评论",
    "7天视频分析·分享",
    "7天视频分析·收藏",
    "7天视频分析·互动量",
    "曝光点赞比",
)
# 只展示原文、不解析数值的列（比值、文字分布）
_TEXT_ONLY_METRICS = frozenset({"赞粉比", "性别分布", "年龄分布", "曝光点赞比"})
DOUYIN_NUMERIC_COLUMNS: tuple[str, ...] = tuple(
    c for c in DOUYIN_METRIC_COLUMNS if c not in _TEXT_ONLY_METRICS
)

MISSING_ACCOUNT_ERROR = (
    "博主ID为空：灰豚导出的这一行没有博主ID，未导入；请在灰豚补齐后重新导出，或在博主页手工新建"
)
QUOTE_NOTE_MAX_LEN = 500
QUOTE_NOTE_TOO_LONG = f"报价备注 超过 {QUOTE_NOTE_MAX_LEN} 字，未写入"
SNAPSHOT_FIELD = "platform_metrics"
_SNAPSHOT_LABEL = "抖音统计"
SNAPSHOT_KEPT_NOTICE = "抖音统计与系统里的不同，按导入规则保留系统值，未写入"

# 固定列表（r1 N3）：失败明细按它遮挡受保护列；不继承手工模版的别名
_BUILTIN_COLUMNS: list[dict[str, Any]] = [
    {"source_col": COL_ACCOUNT, "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": COL_NICKNAME, "target_field": "nickname", "type": "str"},
    {"source_col": COL_WEB_ID, "target_field": "web_id", "type": "str"},
    {"source_col": COL_WECHAT, "target_field": "wechat", "type": "str"},
    {"source_col": COL_QUOTE, "target_field": "quote", "type": "decimal"},
    {"source_col": COL_QUOTE, "target_field": "quote_note", "type": "str"},
    {"source_col": COL_FOLLOWER, "target_field": "follower_count", "type": "int"},
]


def _text(raw: Any) -> str | None:
    """去空白；空 / 占位符 → None。数字格（openpyxl 读成数）已由 runner 转成字符串。"""
    if is_placeholder(raw):
        return None
    text = str(raw).strip()
    return text or None


def _douyin_quote(raw: Any) -> tuple[Decimal | None, str | None]:
    """抖音「报价」→（报价, 报价备注）（D3）：原文一律进报价备注；整格能解析成一个非负金额才同时写报价。"""
    note = _text(raw)
    if note is None:
        return None, None
    try:
        value = parse_cn_number(note)
        quote = check_money(value) if value is not None else None
    except ValueError:
        quote = None
    return quote, note


def _metrics(row: dict[str, Any]) -> dict[str, Any] | None:
    """``platform_metrics``：``raw`` 收非空、非占位的原文；``values`` 收数值列里解析成功的
    （整数存 JSON 整数，否则 JSON 小数）。一格都没有 → None。"""
    raw: dict[str, str] = {}
    for col in DOUYIN_METRIC_COLUMNS:
        text = _text(row.get(col))
        if text is not None:
            raw[col] = text
    if not raw:
        return None
    values: dict[str, int | float] = {}
    for col in DOUYIN_NUMERIC_COLUMNS:
        if col not in raw:
            continue
        try:
            number = parse_cn_number(raw[col])
        except ValueError:
            continue
        if number is None:
            continue
        values[col] = int(number) if number == number.to_integral_value() else float(number)
    return {"source": SOURCE, "raw": raw, "values": values}


class HuitunDouyinImportAdapter(BloggerImportAdapter):
    """灰豚抖音博主库（一行 → 一个抖音博主）。"""

    source: str = SOURCE
    file_layout: ClassVar[XlsxLayout] = XlsxLayout(
        sheet=DOUYIN_SHEET,
        header_marker=COL_ACCOUNT,
        group_prefix_duplicates=True,
        required=True,
        origin="灰豚",
    )
    sensitive_targets: ClassVar[Mapping[str, tuple[str, str]]] = {
        "wechat": ("blogger", "wechat"),
        "quote": ("blogger", "quote"),
        "quote_note": ("blogger", "quote"),
    }
    _REQUIRED_ERRORS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("xiaohongshu_id", MISSING_ACCOUNT_ERROR),
        ("nickname", f"{COL_NICKNAME}不能为空"),
    )
    # 报价备注按冲突规则比较（不总是覆盖）；平台、质量标签同手工模版不比较
    _NOT_COMPARED: ClassVar[frozenset[str]] = frozenset({"platform", "quality_tags"})
    _NOT_FILTERABLE: ClassVar[frozenset[str]] = frozenset()

    def builtin_columns(self) -> list[dict[str, Any]]:
        return _BUILTIN_COLUMNS

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping | None) -> dict[str, Any]:
        """版式固定：按灰豚列名读，不看 mapping。"""
        quote, quote_note = _douyin_quote(row.get(COL_QUOTE))
        return {
            "xiaohongshu_id": _text(row.get(COL_ACCOUNT)),
            "nickname": _text(row.get(COL_NICKNAME)),
            "platform": Platform.DOUYIN.value,
            "web_id": _text(row.get(COL_WEB_ID)),
            "wechat": _text(row.get(COL_WECHAT)),
            "quote": quote,
            "quote_note": quote_note,
            "follower_count": to_int(row.get(COL_FOLLOWER)),
            SNAPSHOT_FIELD: _metrics(row),
        }

    async def _prepare(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        ctx: ImportRowContext,
        warnings: list[str],
    ) -> dict[str, Any]:
        prepared = await super()._prepare(parsed, session=session, ctx=ctx, warnings=warnings)
        note = prepared.get("quote_note")
        if note is not None and len(note) > QUOTE_NOTE_MAX_LEN:
            warnings.append(QUOTE_NOTE_TOO_LONG)
            prepared["quote_note"] = None
        return prepared

    def _identity_mismatch(
        self, existing: Blogger, parsed: dict[str, Any], ctx: ImportRowContext
    ) -> str | None:
        """D1②：同批里同一个博主ID 的网页ID 与先前的行不同 → 可能是不同的人，本行不导。"""
        web_id = normalize(ValueKind.TEXT, parsed.get("web_id"))
        if web_id is None:
            return None
        first = ctx.batch_seen.first_differing_row(
            (OBJECT_TYPE, existing.id, "web_id"), ctx.row_number, web_id
        )
        if first is None:
            return None
        return (
            f"博主ID {parsed['xiaohongshu_id']} 与第 {first} 行相同但网页ID 不同，"
            "可能是不同的人，本行未导入"
        )

    def _insert_extra(self, parsed: dict[str, Any]) -> dict[str, Any]:
        return {"quote_note": parsed.get("quote_note")}

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
        """统计快照（§6.5，r1 N12）：同批以第一行为准；新建 / 库里为空 / OVERWRITE / 带「总是覆盖」标记 → 写；
        不同且没有标记 → 不写、提示。更新时记 ``blogger.update`` 审计（只记「变了」，不记全文）。
        """
        metrics = parsed.get(SNAPSHOT_FIELD)
        if metrics is None:
            return False
        seen_value = json.dumps(metrics["raw"], ensure_ascii=False, sort_keys=True)
        first = ctx.batch_seen.first_differing_row(
            (OBJECT_TYPE, blogger.id, SNAPSHOT_FIELD), ctx.row_number, seen_value
        )
        if first is not None:
            warnings.append(
                f"第 {ctx.row_number} 行的{_SNAPSHOT_LABEL}与第 {first} 行不一致，按第 {first} 行处理"
            )
            return False
        current = blogger.platform_metrics
        if current == metrics:
            return False
        if policy is None:  # 本行新建：随新建写入，不另记审计（同手工新建）
            blogger.platform_metrics = metrics
            await session.flush()
            return True
        if (
            current is not None
            and policy is not DuplicatePolicy.OVERWRITE
            and not always_overwrite(OBJECT_TYPE, SNAPSHOT_FIELD)
        ):
            warnings.append(SNAPSHOT_KEPT_NOTICE)
            return False
        blogger.platform_metrics = metrics
        await session.flush()
        await AuditService(session).log(
            action=APPLIER.audit_action,
            resource=APPLIER.audit_resource,
            resource_id=blogger.id,
            before={},
            after={
                f"{SNAPSHOT_FIELD}_changed": True,
                "via": "import_fill" if current is None else "import_overwrite",
                "import_batch_id": str(ctx.batch_id) if ctx.batch_id is not None else None,
                "row_number": ctx.row_number,
            },
            user_id=ctx.actor_id,
            actor_type="worker",
        )
        return True


__all__ = [
    "DOUYIN_METRIC_COLUMNS",
    "DOUYIN_NUMERIC_COLUMNS",
    "DOUYIN_SHEET",
    "MISSING_ACCOUNT_ERROR",
    "SOURCE",
    "HuitunDouyinImportAdapter",
]
