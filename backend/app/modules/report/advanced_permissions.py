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

# 7e R1（报表视图设计第 4 节），由 migration 061 seed。
#
# 「只看本人」的 action 刻意叫 read_own 而不是 read：pr_manager / operations 持有
# report.*:read，has() 的通配按 action 精确匹配，捞不到 read_own；持 read 的人照旧看全部。
# BI 从 report.production:read 拆出 report.bi:read：给财务 BI 不连带放开投产页；
# report.*:read 仍覆盖它（主管 / 运营不用显式授）。
REPORT_WORK_PROGRESS_READ_OWN = ("report.work_progress", "read_own")
REPORT_PUBLISH_PROGRESS_READ_OWN = ("report.publish_progress", "read_own")
REPORT_BI_READ = ("report.bi", "read")
REPORT_BI_READ_OWN = ("report.bi", "read_own")

REPORT_VIEW_PERMISSIONS: list[tuple[str, str, str]] = [
    (*REPORT_WORK_PROGRESS_READ_OWN, "查看工作进度表（只看本人）"),
    (*REPORT_PUBLISH_PROGRESS_READ_OWN, "查看发文进度表（只看本人）"),
    (*REPORT_BI_READ, "查看 BI 看板"),
    (*REPORT_BI_READ_OWN, "查看 BI 看板（个人版）"),
]


__all__ = [
    "REPORT_ADVANCED_PERMISSIONS",
    "REPORT_BI_READ",
    "REPORT_BI_READ_OWN",
    "REPORT_PUBLISH_PROGRESS_READ_OWN",
    "REPORT_SUMMARY_PERMISSIONS",
    "REPORT_VIEW_PERMISSIONS",
    "REPORT_WORK_PROGRESS_READ_OWN",
]
