"""U06a importer 领域层（纯函数，不依赖 DB / Session）。

- ``csv_safe``：CSV injection 防护（仅失败明细导出时用，P-U06a-05 / NF-6 Q10）
- ``compute_sha256``：流式分块计算文件哈希
- ``safe_filename``：去路径分隔符 / 控制字符（防 R2 key 穿越）
- ``build_mapping_config`` / ``validate_mapping_config``：field_mapping JSONB 构造与校验
"""

from __future__ import annotations

import hashlib
import re
from typing import IO, Any

from app.modules.importer.exceptions import ImportMappingInvalidError

# CSV injection 危险前缀（Excel 公式触发字符）
_DANGEROUS_PREFIX = ("=", "+", "-", "@")

# field_mapping 列类型白名单
_ALLOWED_TYPES = frozenset({"str", "int", "decimal", "date", "datetime", "bool"})

# 文件名安全化：保留中文 / 字母数字 / 常见符号，去路径分隔符与控制字符
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f/\\:*?\"<>|]+")


def csv_safe(value: Any) -> str:
    """CSV injection 防护：以危险字符开头的值加前缀 ``'``（仅导出失败明细时用）。

    导入解析时**不**调用本函数（raw_data 保真）。
    """
    s = "" if value is None else str(value)
    if s and s[0] in _DANGEROUS_PREFIX:
        return "'" + s
    return s


def compute_sha256(stream: IO[bytes], *, chunk_size: int = 8192) -> tuple[str, int]:
    """流式分块计算 SHA256 + 总字节数（内存 O(1)）。

    Returns:
        (hex_digest, size_bytes)。调用方负责后续 ``stream.seek(0)`` 复位。
    """
    h = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(chunk_size), b""):
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size


def safe_filename(filename: str | None) -> str:
    """去除路径分隔符 / 控制字符，防 R2 key 穿越。空 → 'upload'。"""
    if not filename:
        return "upload"
    cleaned = _UNSAFE_FILENAME.sub("_", filename).strip()
    return cleaned or "upload"


def validate_mapping_config(columns: list[dict[str, Any]]) -> dict[str, Any]:
    """校验 + 构造 field_mapping.mapping_config（BR-U06a-25）。

    校验：columns 非空；每列 source_col/target_field 非空；type ∈ 白名单；
    date/datetime 的 transform 必填。

    Returns:
        ``{"columns": [...]}`` JSONB 结构。

    Raises:
        ImportMappingInvalidError: 任一校验失败。
    """
    if not columns:
        raise ImportMappingInvalidError("mapping columns 不能为空")

    normalized: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for i, col in enumerate(columns):
        source_col = str(col.get("source_col", "")).strip()
        target_field = str(col.get("target_field", "")).strip()
        col_type = str(col.get("type", "str")).strip() or "str"
        transform = col.get("transform")

        if not source_col or not target_field:
            raise ImportMappingInvalidError(
                f"第 {i + 1} 列 source_col / target_field 不能为空"
            )
        if col_type not in _ALLOWED_TYPES:
            raise ImportMappingInvalidError(
                f"第 {i + 1} 列 type '{col_type}' 不在白名单 {sorted(_ALLOWED_TYPES)}"
            )
        if col_type in ("date", "datetime") and not transform:
            raise ImportMappingInvalidError(
                f"第 {i + 1} 列 type={col_type} 必须提供 transform（strptime 格式）"
            )
        if target_field in seen_targets:
            raise ImportMappingInvalidError(
                f"target_field '{target_field}' 重复"
            )
        seen_targets.add(target_field)

        normalized.append(
            {
                "source_col": source_col,
                "target_field": target_field,
                "required": bool(col.get("required", False)),
                "type": col_type,
                "transform": transform,
            }
        )

    return {"columns": normalized}


__all__ = [
    "compute_sha256",
    "csv_safe",
    "safe_filename",
    "validate_mapping_config",
]
