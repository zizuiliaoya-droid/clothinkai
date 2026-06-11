"""U04 promotion 模块权限字符串常量。"""

from __future__ import annotations


PROMOTION_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, action, description)
    ("promotion", "read", "查询推广合作"),
    ("promotion", "write", "创建 / 编辑 / 状态推进推广合作"),
    ("promotion", "delete", "软停用推广合作"),
    ("promotion.review", "approve", "PR 主管审核推广（批准/驳回）"),
]
