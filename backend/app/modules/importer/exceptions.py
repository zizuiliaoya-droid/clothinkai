"""U06a importer 模块业务异常（12 个）。

继承自 ``core/exceptions.py`` 的 AppException。错误码矩阵见 functional-design business-rules §7。
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppException, PermissionDeniedError

# ---------------------------------------------------------------------------
# 上传校验（422）
# ---------------------------------------------------------------------------


class ImportSourceUnknownError(AppException):
    """source 不在 ImportAdapterRegistry 白名单。"""

    code = "IMPORT_SOURCE_UNKNOWN"
    status_code = 422
    message = "未知导入来源（source 未注册）"

    def __init__(self, source: str) -> None:
        super().__init__(f"导入来源 '{source}' 未注册", details={"source": source})


class ImportFormatUnsupportedError(AppException):
    code = "IMPORT_FORMAT_UNSUPPORTED"
    status_code = 422
    message = "不支持的文件格式（仅 CSV / XLSX）"


class ImportFileTooLargeError(AppException):
    code = "IMPORT_FILE_TOO_LARGE"
    status_code = 422
    message = "文件超过大小上限"


class ImportTooManyRowsError(AppException):
    code = "IMPORT_TOO_MANY_ROWS"
    status_code = 422
    message = "文件行数超过上限"


class ImportMappingVersionNotFoundError(AppException):
    code = "IMPORT_MAPPING_VERSION_NOT_FOUND"
    status_code = 422
    message = "指定的字段映射版本不存在"


class ImportMappingInvalidError(AppException):
    code = "IMPORT_MAPPING_INVALID"
    status_code = 422
    message = "字段映射配置不合法"


# ---------------------------------------------------------------------------
# 冲突（409）
# ---------------------------------------------------------------------------


class ImportDuplicateFileError(AppException):
    """同 (tenant_id, source, file_hash) 重复文件（EP07-S08）。"""

    code = "IMPORT_DUPLICATE_FILE"
    status_code = 409
    message = "该文件已导入"

    def __init__(self, *, batch_id: Any | None = None) -> None:
        super().__init__(
            f"该文件已导入（batch_id={batch_id}）" if batch_id else "该文件已导入",
            details={"existing_batch_id": str(batch_id) if batch_id else None},
        )


class ImportRetryExhaustedError(AppException):
    """retry_count 已达上限 3（FB-E）。"""

    code = "IMPORT_RETRY_EXHAUSTED"
    status_code = 409
    message = "重试次数已达上限（3 次）"

    def __init__(self, batch_id: Any) -> None:
        super().__init__("重试次数已达上限（3 次）", details={"batch_id": str(batch_id)})


class ImportBatchBusyError(AppException):
    """批次正在处理中，不可并发 retry（NF-3）。"""

    code = "IMPORT_BATCH_BUSY"
    status_code = 409
    message = "批次正在处理中，请稍后重试"

    def __init__(self, batch_id: Any) -> None:
        super().__init__("批次正在处理中，请稍后重试", details={"batch_id": str(batch_id)})


# ---------------------------------------------------------------------------
# 资源未找到（404）
# ---------------------------------------------------------------------------


class ImportBatchNotFoundError(AppException):
    code = "IMPORT_BATCH_NOT_FOUND"
    status_code = 404
    message = "导入批次不存在"

    def __init__(self, batch_id: Any) -> None:
        super().__init__(f"导入批次 {batch_id} 不存在", details={"batch_id": str(batch_id)})


class ImportMappingSpecUnavailableError(AppException):
    """来源没有声明映射目录（mapping_targets），取不到目录（8a-4）。"""

    code = "IMPORT_MAPPING_SPEC_UNAVAILABLE"
    status_code = 404
    message = "该导入来源没有字段映射目录"

    def __init__(self, source: str) -> None:
        super().__init__(f"导入来源 '{source}' 没有字段映射目录", details={"source": source})


class ImportConflictNotFoundError(AppException):
    """裁决时有冲突取不到（不存在、跨租户或来源不可见），整单不处理（8a-6）。"""

    code = "IMPORT_CONFLICT_NOT_FOUND"
    status_code = 404
    message = "导入冲突不存在"

    def __init__(self, missing_ids: list[Any]) -> None:
        super().__init__(
            f"有 {len(missing_ids)} 条冲突不存在或无权查看",
            details={"missing_ids": [str(i) for i in missing_ids]},
        )


# ---------------------------------------------------------------------------
# 冲突（8a-6，422 / 403）
# ---------------------------------------------------------------------------


class ImportConflictExpectedRequiredError(AppException):
    """「用文件覆盖」时每条都必须带 expected_system_values（整单不处理）。"""

    code = "IMPORT_CONFLICT_EXPECTED_REQUIRED"
    status_code = 422
    message = "用文件覆盖时每条冲突都必须带上看到的系统值"

    def __init__(self, ids: list[Any]) -> None:
        super().__init__(
            "用文件覆盖时每条冲突都必须带上看到的系统值",
            details={"ids": [str(i) for i in ids]},
        )


class ImportConflictFieldUnknownError(AppException):
    """按字段筛选冲突时字段不在该来源的比较字段名单里。"""

    code = "IMPORT_CONFLICT_FIELD_UNKNOWN"
    status_code = 422
    message = "未知的冲突字段"

    def __init__(self, field: str) -> None:
        super().__init__(f"未知的冲突字段：{field}", details={"field": field})


class ImportConflictExportTooLargeError(AppException):
    """冲突下载超过条数上限，请缩小筛选范围。"""

    code = "IMPORT_CONFLICT_EXPORT_TOO_LARGE"
    status_code = 422
    message = "冲突条数超过下载上限，请缩小筛选范围"

    def __init__(self, limit: int) -> None:
        super().__init__(f"冲突超过 {limit} 条，请缩小筛选范围后再下载", details={"limit": limit})


class ImportConflictFieldPermissionError(PermissionDeniedError):
    """裁决的冲突里有受保护字段，而用户对它没有读写权限（整单 403）。"""

    code = "FIELD_PERMISSION_DENIED"

    def __init__(self, denied: list[dict[str, Any]]) -> None:
        super().__init__("无权处理含受保护字段的冲突", details={"denied": denied})


# ---------------------------------------------------------------------------
# 存储（500）
# ---------------------------------------------------------------------------


class ImportStorageError(AppException):
    """R2 未配置 / 上传失败（NF-2 补偿后抛）。"""

    code = "IMPORT_STORAGE_ERROR"
    status_code = 500
    message = "文件存储失败"


# ---------------------------------------------------------------------------
# 行级（内部，不直接 HTTP）
# ---------------------------------------------------------------------------


class RowValidationError(AppException):
    """单行 validate 失败（runner 内捕获 → 写 import_job.failed，不冒泡 HTTP）。"""

    code = "IMPORT_ROW_VALIDATION"
    status_code = 422
    message = "行校验失败"


__all__ = [
    "ImportBatchBusyError",
    "ImportBatchNotFoundError",
    "ImportConflictExpectedRequiredError",
    "ImportConflictExportTooLargeError",
    "ImportConflictFieldPermissionError",
    "ImportConflictFieldUnknownError",
    "ImportConflictNotFoundError",
    "ImportDuplicateFileError",
    "ImportFileTooLargeError",
    "ImportFormatUnsupportedError",
    "ImportMappingInvalidError",
    "ImportMappingSpecUnavailableError",
    "ImportMappingVersionNotFoundError",
    "ImportRetryExhaustedError",
    "ImportSourceUnknownError",
    "ImportStorageError",
    "ImportTooManyRowsError",
    "RowValidationError",
]
