"""U06a importer 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class ImportBatchStatus(str, Enum):
    """import_batch 状态机（4 状态，FB-D 无 pending）。

    - PROCESSING: upload 即创建 + Celery 解析中（起点）
    - COMPLETED: 没有失败行（补空 / 重复已跳过 / 冲突都不算失败，8a-6）
    - PARTIAL: 部分行失败（有 import_job.failed 行）
    - FAILED: 解析失败 / 全行失败 / Adapter 缺失

    重试：PARTIAL / FAILED → PROCESSING（原子 claim，NF-3）。
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ImportJobStatus(str, Enum):
    """import_job 行级状态（5 值）。

    - SUCCESS: 新增或已覆盖（与旧来源同义）
    - FILLED: 只补了空、别的都相同（8a-6）
    - SKIPPED: 与系统全等，重复已跳过（8a-6）
    - CONFLICT: 有冲突（已持久化到 import_conflict，8a-6）
    - FAILED: 失败（重试只重跑这一类）
    """

    SUCCESS = "success"
    FAILED = "failed"
    FILLED = "filled"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
