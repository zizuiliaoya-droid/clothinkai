"""U12 凭据模块配置常量。

TEMPORARY: V1+ system_setting 单元落地后改为租户级可配。
"""

from __future__ import annotations

CONSECUTIVE_FAILURE_THRESHOLD = 3
"""连续采集失败 N 次后自动暂停凭据（EP07-S06 / BR-U12-61）。"""


__all__ = ["CONSECUTIVE_FAILURE_THRESHOLD"]
