"""U06a 状态枚举单元测试（ImportBatchStatus / ImportJobStatus）。"""

from __future__ import annotations

from app.modules.importer.enums import ImportBatchStatus, ImportJobStatus


def test_batch_status_values():
    assert ImportBatchStatus.PROCESSING.value == "processing"
    assert ImportBatchStatus.COMPLETED.value == "completed"
    assert ImportBatchStatus.PARTIAL.value == "partial"
    assert ImportBatchStatus.FAILED.value == "failed"
    # FB-D：无 pending
    assert {s.value for s in ImportBatchStatus} == {
        "processing",
        "completed",
        "partial",
        "failed",
    }


def test_job_status_values():
    assert ImportJobStatus.SUCCESS.value == "success"
    assert ImportJobStatus.FAILED.value == "failed"
    assert {s.value for s in ImportJobStatus} == {"success", "failed"}


def test_batch_status_str_enum():
    # str Enum：可直接与字符串比较
    assert ImportBatchStatus.PROCESSING == "processing"
