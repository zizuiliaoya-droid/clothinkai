"""U18 AI 决策建议枚举。"""

from __future__ import annotations

from enum import Enum


class AdviceType(str, Enum):
    STRATEGY = "strategy"    # 推广策略建议
    ANOMALY = "anomaly"      # 异常原因分析
    BLOGGER = "blogger"      # 博主选择建议


class AdviceStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"    # AI 服务不可用降级
    FAILED = "failed"


__all__ = ["AdviceStatus", "AdviceType"]
