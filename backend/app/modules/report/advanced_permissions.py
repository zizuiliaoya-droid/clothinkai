"""U14 报表进阶权限 scope 常量。"""

from __future__ import annotations

# (scope, action, description) — migration 018 seed 用
REPORT_ADVANCED_PERMISSIONS: list[tuple[str, str, str]] = [
    ("report.work_progress", "read", "查看工作进度表"),
    ("report.production", "read", "查看投产报表"),
    ("report.target", "read", "查看爆款约篇目标"),
    ("report.target", "write", "设置爆款约篇目标"),
    ("report.store_daily", "read", "查看店铺数据看板"),
    ("report.store_daily", "write", "编辑店铺数据手动字段"),
    ("report.export", "read", "导出报表 Excel"),
]


__all__ = ["REPORT_ADVANCED_PERMISSIONS"]
