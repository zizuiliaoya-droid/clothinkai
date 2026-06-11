"""U18 AI 模块权限 scope 常量。"""

from __future__ import annotations

# (scope, name, category) — migration 022 seed 用
AI_PERMISSIONS: list[tuple[str, str, str]] = [
    ("ai.advice:read", "调用 AI 决策建议", "function"),
]


__all__ = ["AI_PERMISSIONS"]
