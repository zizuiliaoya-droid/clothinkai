"""U06a importer 模块业务异常（12 个）。

继承自 ``core/exceptions.py`` 的 AppException。错误码矩阵见 functional-design business-rules §7。
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppException


# ---------------------------------------------------------------------------
# 上传校验（422）
# ---------------------------------------------------------------------------


class ImportSourceUnknownError(AppException):
    """source 不在 ImportAdapterRegistry 白名单。"""

    code = "IMPORT_SOURCE_UNKNOWN"
    status_code = 422
    message = "未知导入来源（source 未注册）"

    def __init__(self, source: str) -> None:
        super().__init__(
            f"导入来源 '{source}' 未注册", details={"source": source}
        )


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
        super().__init__(
            "重试次数已达上限（3 次）", details={"batch_id": str(batch_id)}
        )


class ImportBatchBusyError(AppException):
    """批次正在处理中，不可并发 retry（NF-3）。"""

    code = "IMPORT_BATCH_BUSY"
    status_code = 409
    message = "批次正在处理中，请稍后重试"

    def __init__(self, batch_id: Any) -> None:
        super().__init__(
            "批次正在处理中，请稍后重试", details={"batch_id": str(batch_id)}
        )


# ---------------------------------------------------------------------------
# 资源未找到（404）
# ---------------------------------------------------------------------------


class ImportBatchNotFoundError(AppException):
    code = "IMPORT_BATCH_NOT_FOUND"
    status_code = 404
    message = "导入批次不存在"

    def __init__(self, batch_id: Any) -> None:
        super().__init__(
            f"导入批次 {batch_id} 不存在", details={"batch_id": str(batch_id)}
        )


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
    "ImportDuplicateFileError",
    "ImportFileTooLargeError",
    "ImportFormatUnsupportedError",
    "ImportMappingInvalidError",
    "ImportMappingVersionNotFoundError",
    "ImportRetryExhaustedError",
    "ImportSourceUnknownError",
    "ImportStorageError",
    "ImportTooManyRowsError",
    "RowValidationError",
]
