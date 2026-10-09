"""导入比较口径（8a-6，设计 §4.3，补充二 Q1 / Q2，J17）。纯函数，单测覆盖。

一处口径、三处使用：导入时的比较 / 补空、冲突记录与裁决时的「当前值」比对、冲突 CSV，
都用这里的 ``normalize``，不会出现两套口径。

| 情况 | 结果 | 算「比较过」吗 |
|---|---|---|
| 文件没给值（None、空串、空列表） | 不参与比较 | 否 |
| 字段是 ``create_only`` | 不参与比较 | 否 |
| 系统值为空、文件有值 | ``fill_empty`` → 补空；否则冲突 | 是 |
| 两边都有值、归一后相等 / 不同 | 相同 / 冲突 | 是 |

占位符（``-``、``--``、``—``、``——``）由可切换来源的 ``parse_row`` 在取值时就用
``is_placeholder`` 转成 None；``normalize`` / ``diff_fields`` 本身不认占位符——系统里**已有**的
``-`` 按普通值比较，补空不会改掉任何已有值。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

_PLACEHOLDERS = frozenset({"", "-", "--", "—", "——"})
_CENT = Decimal("0.01")
# Numeric(10,2) 的上限：量化后必须 < 10^8
_MONEY_LIMIT = Decimal("100000000")
MONEY_REASON = "必须为非负数字且小于 1 亿"


class ValueKind(StrEnum):
    TEXT = "text"
    DECIMAL = "decimal"
    INT = "int"
    TAGS = "tags"
    REF = "ref"


@dataclass(frozen=True)
class FieldSpec:
    """一个比较字段。"""

    name: str
    label: str
    kind: ValueKind
    create_only: bool = False  # 仅新建时写入，已有对象不比较（补充二 Q2）
    sensitive: tuple[str, str] | None = None  # (entity, field)，对应 FIELD_PERMISSION_REGISTRY
    # 8b R2：两边都有值且不同时以文件为准覆盖、不进冲突。由 duplicate_rules.ALWAYS_OVERWRITE_FIELDS
    # 派生（只给读 specs 的人看）；导入时 adapter 直接调 always_overwrite() 现查，不读这个标记
    always_overwrite: bool = False


@dataclass(frozen=True)
class FieldDiff:
    """一个字段的差异（冲突里的一项）。"""

    field: str
    label: str
    system: JsonValue
    file: JsonValue
    system_display: str | None
    file_display: str | None
    sensitive: tuple[str, str] | None = None

    def to_json(self) -> dict[str, JsonValue]:
        """存进 ``import_conflict.fields`` 的形状（``sensitive`` 存 [entity, field] 或 null）。"""
        return {
            "field": self.field,
            "label": self.label,
            "system": self.system,
            "file": self.file,
            "system_display": self.system_display,
            "file_display": self.file_display,
            "sensitive": list(self.sensitive) if self.sensitive else None,
        }


@dataclass(frozen=True)
class DiffResult:
    compared: frozenset[str]  # 本行实际比较过的字段：文件给了值、且不是 create_only
    fills: dict[str, JsonValue]  # 系统为空、文件有值（fill_empty 时），值已 normalize
    conflicts: list[FieldDiff]  # 两边都有值且不同（fill_empty=False 时也含系统为空的）


def is_placeholder(raw: object) -> bool:
    """None、去空白后为 ``""``、``-``、``--``、``—``、``——`` → 当没给值。"""
    if raw is None:
        return True
    return isinstance(raw, str) and raw.strip() in _PLACEHOLDERS


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, bool):
        raise ValueError("金额格式不正确")
    elif isinstance(value, int | float):
        dec = Decimal(str(value))
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip().replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError("金额格式不正确") from exc
    else:
        raise ValueError("金额格式不正确")
    if not dec.is_finite():
        raise ValueError("金额格式不正确")
    return dec


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("整数格式不正确")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("整数格式不正确")
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return int(text)
        except ValueError:
            dec = _to_decimal(text)
            if dec != dec.to_integral_value():
                raise ValueError("整数格式不正确") from None
            return int(dec)
    raise ValueError("整数格式不正确")


def _quantized_text(dec: Decimal) -> str:
    """按 0.01 ROUND_HALF_UP 量化后的字符串；超出 Decimal 精度的天文数字原样输出（校验另做）。"""
    try:
        return str(dec.quantize(_CENT, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return str(dec)


def _items(value: object) -> list[object]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def json_value(kind: ValueKind, value: object) -> JsonValue:
    """原值转 JSON 值（审计与冲突存值用）：金额量化成字符串、UUID 转字符串、INT 转 int、
    TAGS 排序后的字符串列表；TEXT 原样（**不去空白**，审计记实际值）；None → None。
    """
    if value is None:
        return None
    if kind is ValueKind.DECIMAL:
        return _quantized_text(_to_decimal(value))
    if kind is ValueKind.INT:
        return _to_int(value)
    if kind is ValueKind.TAGS:
        tags: list[JsonValue] = []
        tags.extend(sorted(str(x) for x in _items(value) if x is not None))
        return tags
    if kind is ValueKind.REF:
        return str(value) if isinstance(value, UUID) else str(UUID(str(value)))
    return str(value)


def normalize(kind: ValueKind, value: object) -> JsonValue:
    """在 ``json_value`` 之上：TEXT 去首尾空白、空串当空；TAGS 去空白、去重、排序，空列表当空；
    非文本类型的空白串当空。返回 None = 空。可比较、可存 JSON。
    """
    if value is None:
        return None
    if isinstance(value, str) and kind is not ValueKind.TEXT and not value.strip():
        return None
    if kind is ValueKind.TEXT:
        text = str(value).strip()
        return text or None
    if kind is ValueKind.TAGS:
        cleaned = {str(x).strip() for x in _items(value) if x is not None and str(x).strip()}
        tags: list[JsonValue] = []
        tags.extend(sorted(cleaned))
        return tags or None
    return json_value(kind, value)


def display_value(value: JsonValue) -> str | None:
    """界面 / CSV 上的显示文本（标签用「、」连接）。"""
    if value is None:
        return None
    if isinstance(value, list):
        return "、".join(str(x) for x in value)
    return str(value)


def diff_fields(
    specs: Iterable[FieldSpec],
    system: Mapping[str, object],
    incoming: Mapping[str, object],
    *,
    fill_empty: bool,
    displays: Mapping[str, tuple[str | None, str | None]] | None = None,
) -> DiffResult:
    """按 §4.3 的口径比较一个对象。

    ``system``：对象当前值（applier.current_values，已 normalize，这里再归一一次是幂等的）；
    ``incoming``：文件值（缺键 / None / 空 = 没给值）；``displays``：REF 字段的显示名
    （字段名 → (系统显示, 文件显示)），其余字段的显示文本由值本身生成。
    """
    compared: set[str] = set()
    fills: dict[str, JsonValue] = {}
    conflicts: list[FieldDiff] = []
    for spec in specs:
        if spec.create_only or spec.name not in incoming:
            continue
        file_v = normalize(spec.kind, incoming[spec.name])
        if file_v is None:
            continue
        compared.add(spec.name)
        sys_v = normalize(spec.kind, system.get(spec.name))
        if sys_v is not None and sys_v == file_v:
            continue
        if sys_v is None and fill_empty:
            fills[spec.name] = file_v
            continue
        sys_disp, file_disp = (displays or {}).get(
            spec.name, (display_value(sys_v), display_value(file_v))
        )
        conflicts.append(
            FieldDiff(
                field=spec.name,
                label=spec.label,
                system=sys_v,
                file=file_v,
                system_display=sys_disp,
                file_display=file_disp,
                sensitive=spec.sensitive,
            )
        )
    return DiffResult(compared=frozenset(compared), fills=fills, conflicts=conflicts)


def check_money(value: object) -> Decimal:
    """金额校验（导入校验、SKU / 博主 applier 共用）：Decimal、≥ 0、按 0.01 ``ROUND_HALF_UP``
    量化后 < 10^8（``Numeric(10,2)``）；返回量化后的值。不合法抛 ``ValueError``（文案不含值）。
    """
    try:
        dec = _to_decimal(value)
    except ValueError:
        raise ValueError(MONEY_REASON) from None
    if dec < 0 or dec >= _MONEY_LIMIT:
        raise ValueError(MONEY_REASON)
    quantized = dec.quantize(_CENT, rounding=ROUND_HALF_UP)
    if quantized >= _MONEY_LIMIT:  # 99999999.995 量化后进位到 1 亿
        raise ValueError(MONEY_REASON)
    return quantized


__all__ = [
    "MONEY_REASON",
    "DiffResult",
    "FieldDiff",
    "FieldSpec",
    "ValueKind",
    "check_money",
    "diff_fields",
    "display_value",
    "is_placeholder",
    "json_value",
    "normalize",
]
