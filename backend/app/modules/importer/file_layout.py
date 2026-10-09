"""导入文件版式声明（8b §6.1）：adapter 用类属性 ``file_layout`` 声明读哪个 sheet、哪行是表头、重名列怎么改名。

没声明（``None``）时 runner 照旧：活动 sheet、第一个非空行当表头。CSV 不看版式。
"""

from __future__ import annotations

from dataclasses import dataclass

# 表头标记只在前这么多个非空行里找
HEADER_SCAN_ROWS = 10


class LayoutNotFoundError(ValueError):
    """``required=True`` 的版式在文件里找不到：批次 failed，``str(exc)`` 直接作 ``error_summary``（给用户看）。"""


@dataclass(frozen=True)
class XlsxLayout:
    sheet: str | None = None  # 有同名 sheet 就读它，否则 wb.active
    header_marker: str | None = (
        None  # 前 10 个非空行里第一个有单元格（去空白）等于它的行是表头；找不到 → 第一个非空行
    )
    group_prefix_duplicates: bool = False  # 表头上方紧挨的非空行是分组行；重名列改成「分组·列名」
    required: bool = False  # 声明了 sheet / 表头标记却找不到 → LayoutNotFoundError，不静默回落
    origin: str = ""  # 出错提示里「请上传{origin}导出的原文件」

    def not_found_message(self) -> str:
        parts = []
        if self.sheet:
            parts.append(f"『{self.sheet}』sheet")
        if self.header_marker:
            parts.append(f"『{self.header_marker}』表头")
        return f"没有找到{' 或'.join(parts)}，请上传{self.origin}导出的原文件"


def rename_duplicate_headers(header: list[str], group_row: list[str] | None) -> list[str]:
    """重名的非空列名改成「分组·列名」；分组从分组行同列向左就近取，取不到或改完仍重名加 ``#2``、``#3``…。"""
    counts: dict[str, int] = {}
    for name in header:
        if name:
            counts[name] = counts.get(name, 0) + 1
    renamed: list[str] = []
    used: set[str] = set()
    for j, name in enumerate(header):
        if not name:
            renamed.append(name)
            continue
        new = name
        if counts[name] > 1:
            group = _group_for(group_row, j)
            if group:
                new = f"{group}·{name}"
        if new in used:
            k = 2
            while f"{new}#{k}" in used:
                k += 1
            new = f"{new}#{k}"
        used.add(new)
        renamed.append(new)
    return renamed


def _group_for(group_row: list[str] | None, col: int) -> str:
    if not group_row:
        return ""
    for j in range(min(col, len(group_row) - 1), -1, -1):
        if group_row[j]:
            return group_row[j]
    return ""
