"""U03 blogger 模块权限字符串常量。"""

from __future__ import annotations

BLOGGER_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, action, description)
    ("blogger", "read", "查询博主"),
    ("blogger", "write", "创建 / 编辑博主"),
    ("blogger", "delete", "软删 / 恢复博主"),
    # 独立一级域：blogger.*:* 捞不到（见 auth/default_roles.py::BLOGGER_TAG_WRITE）
    ("blogger_tag", "write", "维护博主标签字典"),
]
