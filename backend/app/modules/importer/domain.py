"""U06a importer 领域层（纯函数，不依赖 DB / Session）。

- ``csv_safe``：CSV injection 防护（仅失败明细导出时用，P-U06a-05 / NF-6 Q10）
- ``compute_sha256``：流式分块计算文件哈希
- ``safe_filename``：去路径分隔符 / 控制字符（防 R2 key 穿越）
- ``build_mapping_config`` / ``validate_mapping_config``：field_mapping JSONB 构造与校验
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TargetSpec:
    """映射目录里的一个目标字段（8a-4，设计 §4.7，J18）。

    adapter 声明 ``mapping_targets: ClassVar[tuple[TargetSpec, ...]]`` 后：内置默认映射由目录生成
    （``builtin_columns_from_targets``），保存自定义映射时按目录校验（``validate_mapping_config``
    的 ``targets``）。
    """

    field: str
    label: str
    type: str  # 取 _ALLOWED_TYPES 之一，由系统定（忽略客户端传的）
    default_col: str
    aliases: tuple[str, ...] = ()
    required: bool = False
    # 「其一必填」：``组名:选项``。同组里至少有一个选项的全部字段都映射了，如颜色组的
    # ``color_size:combined``（颜色及规格）与 ``color_size:split``（颜色 + 规格）
    group: str | None = None
    create_only: bool = False  # 仅新建时写入（补充二 Q2），比较时跳过
    sensitive: tuple[str, str] | None = None  # 受字段权限保护（§4.6.1 遮挡失败明细用）


def builtin_columns_from_targets(targets: Iterable[TargetSpec]) -> list[dict[str, Any]]:
    """由目录生成内置默认映射（``source_col`` = ``default_col``，带别名；与旧 ``_DEFAULT_COLUMNS`` 同形）。"""
    return [
        {
            "source_col": t.default_col,
            "target_field": t.field,
            "type": t.type,
            "aliases": list(t.aliases),
        }
        for t in targets
    ]


def _check_against_targets(columns: list[dict[str, Any]], targets: tuple[TargetSpec, ...]) -> None:
    """目录校验：必填已映射、「其一必填」组至少满足一个选项。"""
    mapped = {c["target_field"] for c in columns}
    for t in targets:
        if t.required and t.field not in mapped:
            raise ImportMappingInvalidError(f"必填字段「{t.label}」没有映射")
    groups: dict[str, dict[str, list[TargetSpec]]] = {}
    for t in targets:
        if t.group:
            name, _, option = t.group.partition(":")
            groups.setdefault(name, {}).setdefault(option, []).append(t)
    for options in groups.values():
        if not any(all(t.field in mapped for t in members) for members in options.values()):
            text = "，或".join(" + ".join(t.label for t in members) for members in options.values())
            raise ImportMappingInvalidError(f"至少要映射一组：{text}")


def validate_mapping_config(
    columns: list[dict[str, Any]], *, targets: Iterable[TargetSpec] | None = None
) -> dict[str, Any]:
    """校验 + 构造 field_mapping.mapping_config（BR-U06a-25）。

    校验：columns 非空；每列 source_col/target_field 非空；type ∈ 白名单；
    date/datetime 的 transform 必填；目标字段不重复（一个目标字段只读一列，不存别名）。

    给了 ``targets``（adapter 的映射目录，8a-4）时再加：目标字段必须在目录里、``required`` 的
    必须映射、``group`` 至少满足一种组合；``type`` / ``required`` 一律用目录里的值。

    Returns:
        ``{"columns": [...]}`` JSONB 结构。

    Raises:
        ImportMappingInvalidError: 任一校验失败。
    """
    if not columns:
        raise ImportMappingInvalidError("mapping columns 不能为空")

    catalog = {t.field: t for t in targets} if targets is not None else None
    normalized: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for i, col in enumerate(columns):
        source_col = str(col.get("source_col", "")).strip()
        target_field = str(col.get("target_field", "")).strip()
        col_type = str(col.get("type", "str")).strip() or "str"
        transform = col.get("transform")
        required = bool(col.get("required", False))

        if not source_col or not target_field:
            raise ImportMappingInvalidError(f"第 {i + 1} 列 source_col / target_field 不能为空")
        if catalog is not None:
            spec = catalog.get(target_field)
            if spec is None:
                raise ImportMappingInvalidError(f"不认识的目标字段 {target_field}")
            col_type = spec.type
            required = spec.required
        if col_type not in _ALLOWED_TYPES:
            raise ImportMappingInvalidError(
                f"第 {i + 1} 列 type '{col_type}' 不在白名单 {sorted(_ALLOWED_TYPES)}"
            )
        if col_type in ("date", "datetime") and not transform:
            raise ImportMappingInvalidError(
                f"第 {i + 1} 列 type={col_type} 必须提供 transform（strptime 格式）"
            )
        if target_field in seen_targets:
            raise ImportMappingInvalidError(f"target_field '{target_field}' 重复")
        seen_targets.add(target_field)

        normalized.append(
            {
                "source_col": source_col,
                "target_field": target_field,
                "required": required,
                "type": col_type,
                "transform": transform,
            }
        )

    if catalog is not None:
        _check_against_targets(normalized, tuple(catalog.values()))
    return {"columns": normalized}


__all__ = [
    "TargetSpec",
    "builtin_columns_from_targets",
    "compute_sha256",
    "csv_safe",
    "safe_filename",
    "validate_mapping_config",
]
