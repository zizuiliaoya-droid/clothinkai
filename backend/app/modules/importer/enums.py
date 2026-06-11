"""U06a importer 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class ImportBatchStatus(str, Enum):
    """import_batch 状态机（4 状态，FB-D 无 pending）。

    - PROCESSING: upload 即创建 + Celery 解析中（起点）
    - COMPLETED: 全部行成功
    - PARTIAL: 部分行失败（有 import_job.failed 行）
    - FAILED: 解析失败 / 全行失败 / Adapter 缺失

    重试：PARTIAL / FAILED → PROCESSING（原子 claim，NF-3）。
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ImportJobStatus(str, Enum):
    """import_job 行级状态（2 值）。"""

    SUCCESS = "success"
    FAILED = "failed"
