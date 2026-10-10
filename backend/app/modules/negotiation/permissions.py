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

# 流程线 M2（迁移 061）。都是独立一级域：叫 negotiation.final:approve 的话，哪天有人给主管加
# negotiation.*:*，主管就顺带拿到老板权；negotiation:read PR 也有，分不出「只看自己」和「看全部」。
SCOPE_NEGOTIATION_FINAL = "negotiation_final"
SCOPE_NEGOTIATION_FINAL_APPROVE = "negotiation_final:approve"
"""老板定稿 / 退回（N6、N7）。只入册不绑角色，管理员靠 ``*``。"""
SCOPE_NEGOTIATION_ALL = "negotiation_all"
SCOPE_NEGOTIATION_ALL_READ = "negotiation_all:read"
"""看全部谈款（草稿除外，7c-10）：pr_manager、finance、operations。"""

NEGOTIATION_PERMISSIONS: list[tuple[str, str, str]] = [
    ("negotiation", "read", "查看谈款单"),
    ("negotiation", "write", "新建 / 编辑草稿 / 提交审核"),
    ("negotiation.review", "approve", "审核谈款单（通过 / 驳回）"),
    ("negotiation_final", "approve", "老板定稿 / 退回谈款"),
    ("negotiation_all", "read", "查看全部谈款（草稿除外）"),
]


__all__ = [
    "NEGOTIATION_PERMISSIONS",
    "SCOPE_NEGOTIATION",
    "SCOPE_NEGOTIATION_ALL",
    "SCOPE_NEGOTIATION_ALL_READ",
    "SCOPE_NEGOTIATION_FINAL",
    "SCOPE_NEGOTIATION_FINAL_APPROVE",
    "SCOPE_NEGOTIATION_READ",
    "SCOPE_NEGOTIATION_REVIEW",
    "SCOPE_NEGOTIATION_REVIEW_APPROVE",
    "SCOPE_NEGOTIATION_WRITE",
]
