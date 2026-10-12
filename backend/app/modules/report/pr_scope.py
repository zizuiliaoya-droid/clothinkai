"""报表「只看本人」的 PR 范围（7e R1，报表视图设计第 4 节）。

工作进度主表、下钻、发文进度 4 个接口都走 :func:`resolve_pr_scope`：路由依赖只挡
「read / read_own 一个都没有」，持哪一个由这里在 service 层再判 —— ``has()`` 的通配
按 action 精确匹配，``report.*:read`` 捞不到 ``read_own``，所以这条规则通配挡不住。

- 持 ``<prefix>:read``（或 ``also_full`` 任一）→ 按请求的 ``pr_id``：不传 = 全部、UUID = 该 PR、
  ``unassigned`` = 未分配
- 只持 ``<prefix>:read_own`` → 一律本人，请求的 ``pr_id`` 忽略（不报错，免得探测别人）
- 都没有 → 403
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.core.security.permissions import EffectivePermissions

UNASSIGNED: Literal["unassigned"] = "unassigned"

# 请求里的 pr_id：None = 不限、UUID = 指定 PR、"unassigned" = 未分配
PrParam = UUID | Literal["unassigned"] | None


class PrScopeKind(StrEnum):
    ALL = "all"
    PR = "pr"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True)
class PrScope:
    kind: PrScopeKind
    pr_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.kind is PrScopeKind.PR) != (self.pr_id is not None):
            raise ValueError("只有 kind=PR 时带 pr_id")

    def keeps(self, pr_id: UUID | None) -> bool:
        """service 层 SQL 之后按行过滤用：这一行的 PR 是否在范围内。"""
        if self.kind is PrScopeKind.ALL:
            return True
        if self.kind is PrScopeKind.UNASSIGNED:
            return pr_id is None
        return pr_id == self.pr_id

    def sql(self, column: str) -> tuple[str, dict[str, Any]]:
        """下推到 SQL 用：返回 ``(" AND <column> …", 参数)``，ALL 时为 ``("", {})``。

        子句带前导 `` AND ``，直接接在已有 WHERE 后面。
        """
        if self.kind is PrScopeKind.ALL:
            return "", {}
        if self.kind is PrScopeKind.UNASSIGNED:
            return f" AND {column} IS NULL", {}
        return f" AND {column} = CAST(:scope_pr_id AS uuid)", {"scope_pr_id": self.pr_id}

    def as_param(self) -> PrParam:
        """反过来转成 ``PrParam``，传给收 ``pr_id`` 参数的仓储方法（7e R2 下推到 SQL）。"""
        if self.kind is PrScopeKind.ALL:
            return None
        if self.kind is PrScopeKind.UNASSIGNED:
            return UNASSIGNED
        return self.pr_id


PR_SCOPE_ALL = PrScope(PrScopeKind.ALL)


def pr_scope_from_param(requested: PrParam) -> PrScope:
    """``PrParam`` → ``PrScope``：None = 全部、``unassigned`` = 未分配、UUID = 该 PR。

    只做转换、不判权：判权在 :func:`resolve_pr_scope`。
    """
    if requested is None:
        return PR_SCOPE_ALL
    if requested == UNASSIGNED:
        return PrScope(PrScopeKind.UNASSIGNED)
    assert isinstance(requested, UUID)
    return PrScope(PrScopeKind.PR, requested)


def resolve_pr_scope(
    perms: EffectivePermissions,
    user_id: UUID,
    requested: PrParam,
    scope_prefix: str,
    *,
    also_full: Iterable[tuple[str, str]] = (),
) -> PrScope:
    """按当前用户的权限定 PR 范围（规则见模块 docstring）。

    ``also_full``：另外哪些 (scope, action) 也算「看全部」（下钻接口给 ``report.bi:read``）。
    """
    if perms.has(scope_prefix, "read") or any(perms.has(s, a) for s, a in also_full):
        return pr_scope_from_param(requested)
    if perms.has(scope_prefix, "read_own"):
        return PrScope(PrScopeKind.PR, user_id)
    raise PermissionDeniedError(
        f"缺少权限 {scope_prefix}:read",
        details={"required_scope": scope_prefix, "required_action": "read"},
    )


def parse_pr_param(raw: str | None) -> PrParam:
    """查询参数 ``pr_id``：不传 → None；``unassigned`` → 未分配；否则必须是 UUID（错 422）。"""
    if raw is None:
        return None
    if raw == UNASSIGNED:
        return UNASSIGNED
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValidationError(
            "pr_id 须为 UUID 或 unassigned", details={"field": "pr_id", "value": raw}
        ) from exc


__all__ = [
    "PR_SCOPE_ALL",
    "UNASSIGNED",
    "PrParam",
    "PrScope",
    "PrScopeKind",
    "parse_pr_param",
    "pr_scope_from_param",
    "resolve_pr_scope",
]
