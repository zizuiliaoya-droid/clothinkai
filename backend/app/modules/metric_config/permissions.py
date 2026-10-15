"""平台指标配置的权限 scope（迁移 066）。

``metric_config`` 必须是独立一级域：``has()`` 的前缀通配只看第一段。叫 ``promotion.metric:write``
会被 PR 的 ``promotion.*:*`` 命中（PR 能自己调爆文阈值）；叫 ``report.metric:read`` 会被
``report.*:read`` 捞走读权限。读授 pr_manager、operations，写只授 pr_manager，管理员靠 ``*``。
"""

from __future__ import annotations

SCOPE_METRIC_CONFIG = "metric_config"
SCOPE_METRIC_CONFIG_READ = "metric_config:read"
SCOPE_METRIC_CONFIG_WRITE = "metric_config:write"

__all__ = [
    "SCOPE_METRIC_CONFIG",
    "SCOPE_METRIC_CONFIG_READ",
    "SCOPE_METRIC_CONFIG_WRITE",
]
