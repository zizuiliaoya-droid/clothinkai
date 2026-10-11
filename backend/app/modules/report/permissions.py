"""U08 report 模块权限常量。

``report.publish_progress:read``：pr_manager / operations 通过 ``report.*:read`` 通配持有。
pr 原来直含，061（7e R1）起收回，改授 ``report.publish_progress:read_own``（只看本人，
常量见 ``advanced_permissions.REPORT_PUBLISH_PROGRESS_READ_OWN``）。本文件仅声明常量。
"""

from __future__ import annotations

REPORT_PUBLISH_PROGRESS_READ = ("report.publish_progress", "read")

__all__ = ["REPORT_PUBLISH_PROGRESS_READ"]
