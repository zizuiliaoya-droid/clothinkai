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

# PRD 模块三的手动刷新。独立成一个 scope 而不是复用上面任何一条：
# 这个动作横跨 5 张汇总表，而且会先删区间内的行再重建 —— 挂在
# report.production 或 report.store_daily 下，持有单张报表读写权的人就能
# 触发一次全表重算，权限边界说不清。由 migration 052 seed，只授 admin。
REPORT_SUMMARY_PERMISSIONS: list[tuple[str, str, str]] = [
    ("report.summary", "refresh", "手动刷新报表汇总表"),
]


__all__ = ["REPORT_ADVANCED_PERMISSIONS", "REPORT_SUMMARY_PERMISSIONS"]
