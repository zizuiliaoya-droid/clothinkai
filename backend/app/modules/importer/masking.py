"""失败明细 CSV 按查看者的字段权限脱敏（8a-7；设计 §4.6.1）。

失败明细的 ``raw_data`` 是原始行，键是文件里的列名。有受保护目标字段的 adapter 声明
``sensitive_targets``（目标字段 → ``FIELD_PERMISSION_REGISTRY`` 的 ``(entity, field)``）并提供
``builtin_columns()``（内置默认映射，含别名）；没有声明的来源不遮（现状）。

要遮的列 =（批次所用映射里受保护目标字段的 ``source_col`` 与 ``aliases``）∪（内置默认映射里
同样的列），只收查看者对对应字段**没有读权限**的。并上内置默认这一份，是因为自定义映射把成本价
指到「进价」时，同一份导出里仍在的「成本价」原列不在批次映射里，也要遮；多遮一列只会少显示。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.security.field_permissions import FieldPermissionContext, can_read_field

MASK = "***"


def _builtin_columns(adapter: object) -> list[Mapping[str, Any]]:
    fn = getattr(adapter, "builtin_columns", None)
    return list(fn()) if callable(fn) else []


def masked_source_columns(
    adapter: object,
    columns: Iterable[Mapping[str, Any]] | None,
    field_ctx: FieldPermissionContext,
) -> frozenset[str]:
    """查看者看不到的原始列名集合。

    Args:
        adapter: 批次来源的 adapter（未注册来源传 None → 不遮）。
        columns: 批次所用映射的 ``mapping_config["columns"]``；没有版本传 None
            （内置默认映射总会并入）。
        field_ctx: 查看者的字段权限上下文。
    """
    sensitive: Mapping[str, tuple[str, str]] = getattr(adapter, "sensitive_targets", None) or {}
    hidden = {
        target
        for target, (entity, field) in sensitive.items()
        if not can_read_field(entity, field, field_ctx)
    }
    if not hidden:
        return frozenset()
    names: set[str] = set()
    for col in [*(columns or []), *_builtin_columns(adapter)]:
        if col.get("target_field") not in hidden:
            continue
        source_col = col.get("source_col")
        if source_col:
            names.add(str(source_col))
        names.update(str(alias) for alias in col.get("aliases") or [])
    return frozenset(names)


def mask_raw_data(raw: Mapping[str, Any] | None, masked: frozenset[str]) -> Any:
    """复制原始行，把要遮的列上的非空值换成「***」（空值原样保留）。"""
    if not raw or not masked:
        return raw
    return {
        key: MASK if key in masked and value not in (None, "") else value
        for key, value in raw.items()
    }


__all__ = ["MASK", "mask_raw_data", "masked_source_columns"]
