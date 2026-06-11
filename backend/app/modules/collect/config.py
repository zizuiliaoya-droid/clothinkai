"""U13 采集模块配置常量。

TEMPORARY: V1+ system_setting 单元落地后改为租户级可配。
"""

from __future__ import annotations

WORKER_AUTH_FAILURE_THRESHOLD = 5
"""worker_token 连续鉴权失败 N 次自动吊销（§2.2.1 / BR-U13-04）。"""

CRED_TOKEN_TTL_SECONDS = 300
"""一次性 cred_token 有效期（5 分钟，§2.2.1 / BR-U13-21）。"""


__all__ = ["CRED_TOKEN_TTL_SECONDS", "WORKER_AUTH_FAILURE_THRESHOLD"]
