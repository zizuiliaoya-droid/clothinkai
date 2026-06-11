"""U08 report 模块权限常量。

``report.publish_progress:read`` 已在 default_roles seed（pr 直含；pr_manager/operations
通过 report.*:read 通配覆盖）；本文件仅声明常量，不新增 migration seed。
"""

from __future__ import annotations

REPORT_PUBLISH_PROGRESS_READ = ("report.publish_progress", "read")

__all__ = ["REPORT_PUBLISH_PROGRESS_READ"]
