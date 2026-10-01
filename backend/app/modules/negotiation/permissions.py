"""谈款审核权限 scope。

**刻意用独立一级域 ``negotiation``**，不挂在 ``promotion.`` 下。

``EffectivePermissions.has`` 的前缀通配只看 scope 第一段：PR 持有 ``promotion.*:*``，
所以任何 ``promotion.xxx:yyy`` 都会被它命中。谈款审核如果叫
``promotion.negotiation:approve``，PR 会自动拿到主管的审核权限。

（既有的 ``promotion.review:approve`` 就有这个问题 —— PR 实际能审核别人的推广单，
只靠 service 的 ``SelfReviewForbiddenError`` 挡住自审。这里不重犯。）

同理，授给 PR 的是两条具体 scope 而不是 ``negotiation.*:*``：给了通配，
``negotiation.review:approve`` 又会被命中。
"""

from __future__ import annotations

SCOPE_NEGOTIATION = "negotiation"
SCOPE_NEGOTIATION_READ = "negotiation:read"
SCOPE_NEGOTIATION_WRITE = "negotiation:write"

SCOPE_NEGOTIATION_REVIEW = "negotiation.review"
SCOPE_NEGOTIATION_REVIEW_APPROVE = "negotiation.review:approve"

NEGOTIATION_PERMISSIONS: list[tuple[str, str, str]] = [
    ("negotiation", "read", "查看谈款单"),
    ("negotiation", "write", "新建 / 编辑草稿 / 提交审核"),
    ("negotiation.review", "approve", "审核谈款单（通过 / 驳回）"),
]


__all__ = [
    "NEGOTIATION_PERMISSIONS",
    "SCOPE_NEGOTIATION",
    "SCOPE_NEGOTIATION_READ",
    "SCOPE_NEGOTIATION_REVIEW",
    "SCOPE_NEGOTIATION_REVIEW_APPROVE",
    "SCOPE_NEGOTIATION_WRITE",
]
