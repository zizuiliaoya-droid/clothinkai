"""催发任务权限 scope。

这里有两个不同的一级域，是故意的：

``promotion.urge:read/write`` —— **挂在 promotion 下正是想要的**。催发是 PR 的日常
工作，PR 持 ``promotion.*:*``，前缀通配自动覆盖；运营持 ``promotion.*:read``，
自动获得只读而拿不到写（通配只匹配 ``promotion.*:*`` 和 ``promotion.*:read``）。
仓库角色持的是 ``promotion:read`` + ``promotion.warehouse:write``，两条都匹配不上
``promotion.urge``，所以仓库看不到催发，符合预期。

``urge_config:read/write`` —— **必须独立一级域**。如果叫
``promotion.urge_config:write``，PR 的 ``promotion.*:*`` 会把它一起命中，
于是 PR 能自己把「超过 3 次提示主管」的阈值改成 999。改阈值是管理层的事。
"""

from __future__ import annotations

SCOPE_URGE = "promotion.urge"
SCOPE_URGE_READ = "promotion.urge:read"
SCOPE_URGE_WRITE = "promotion.urge:write"

SCOPE_URGE_CONFIG = "urge_config"
SCOPE_URGE_CONFIG_READ = "urge_config:read"
SCOPE_URGE_CONFIG_WRITE = "urge_config:write"

URGE_PERMISSIONS: list[tuple[str, str, str]] = [
    ("promotion.urge", "read", "查看催发任务与留痕"),
    ("promotion.urge", "write", "发起催发 / 关闭催发任务"),
    ("urge_config", "read", "查看催发阈值配置"),
    ("urge_config", "write", "修改催发阈值配置"),
]


__all__ = [
    "SCOPE_URGE",
    "SCOPE_URGE_CONFIG",
    "SCOPE_URGE_CONFIG_READ",
    "SCOPE_URGE_CONFIG_WRITE",
    "SCOPE_URGE_READ",
    "SCOPE_URGE_WRITE",
    "URGE_PERMISSIONS",
]
