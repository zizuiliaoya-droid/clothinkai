"""U13 采集模块权限 scope 常量。"""

from __future__ import annotations

# (scope, action, description) — migration 017 seed 用
CRAWLER_PERMISSIONS: list[tuple[str, str, str]] = [
    ("crawler.worker", "write", "签发/吊销采集 Worker Token"),
    ("crawler.task", "read", "查看采集任务（运维监控）"),
    ("data_quality", "read", "查看数据质量看板"),
    ("data_quality", "write", "处理数据质量异常（fixed/ignored）"),
]


__all__ = ["CRAWLER_PERMISSIONS"]
