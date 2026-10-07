"""导入的来源级权限（8a-7，补充二 Q5；设计 §4.6）。

importer 的路由一律**不挂路由级权限依赖**：来源在请求体或批次记录里，路由级依赖表达不了；
而且依赖先于处理函数执行，会先把只有 ``product.*:*`` 的跟单挡在外面。权限在处理函数 /
service 里按来源调本模块判断。

- 商品资料（``manual_style_sku``）的上传 / 重试 / 改映射 / 查看 / 裁决**只认**
  ``product.import:write``：跟单与运营（``product.*:*``）、管理员（``*``）命中；PR / 主管只有
  ``importer.*`` 与 W1 给的精确 ``product.style:read``，不命中——这就是从 PR / 主管收回的方式，
  不改他们的角色数据
- 博主（``manual_blogger``）的裁决看 ``blogger:write``，其余同默认
- 其他来源（含未注册的来源）走默认规则，与 8a 之前一致
- 批次可见性 = 原有 ``importer.batch:read`` **或**来源自己的 ``view`` 权限

以后给别的来源加按来源的权限（如推广单 / 结款单），在 ``SOURCE_ACCESS`` 里加一行即可，
不要再在路由上挂一层依赖（护栏 ``test_import_access_api.py::test_no_route_level_permission``）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.modules.importer.duplicate_rules import is_configurable
from app.modules.importer.registry import ImportAdapterRegistry

SCOPE_PRODUCT_IMPORT = "product.import"


@dataclass(frozen=True)
class SourceAccess:
    """一个导入来源的四类权限，每项是 ``(scope, action)``。"""

    write: tuple[str, str]  # 上传、重试
    mapping: tuple[str, str]  # 保存 / 重置字段映射
    view: (
        tuple[str, str] | None
    )  # 除 importer.batch:read 外，还能看该来源批次 / 映射 / 冲突 / 失败明细的权限
    resolve: tuple[str, str]  # 裁决冲突（另叠加字段级权限）


_DEFAULT = SourceAccess(
    write=("importer.batch", "write"),
    mapping=("importer.mapping", "write"),
    view=None,
    resolve=("importer.batch", "write"),
)

SOURCE_ACCESS: dict[str, SourceAccess] = {
    "manual_style_sku": SourceAccess(
        write=(SCOPE_PRODUCT_IMPORT, "write"),
        mapping=(SCOPE_PRODUCT_IMPORT, "write"),
        view=(SCOPE_PRODUCT_IMPORT, "write"),
        resolve=(SCOPE_PRODUCT_IMPORT, "write"),
    ),
    "manual_blogger": replace(_DEFAULT, resolve=("blogger", "write")),
}

# 来源的中文名（导入记录页、权限说明用）；没列的来源显示来源编码本身
SOURCE_LABELS: dict[str, str] = {
    "manual_style_sku": "商品资料",
    "manual_blogger": "博主",
    "manual_promotion": "推广单",
    "manual_settlement": "结款单",
    "qianniu": "千牛日报",
    "wanxiangtai": "万相台日报",
    "manual_tao_order": "拍单",
    "manual_brush_order": "刷单",
    "huitun": "灰豚博主画像",
}

_BATCH_READ: tuple[str, str] = ("importer.batch", "read")


def access_for(source: str) -> SourceAccess:
    """取来源的权限声明；未声明的来源走默认规则。"""
    return SOURCE_ACCESS.get(source, _DEFAULT)


def can_view(perms: EffectivePermissions, source: str) -> bool:
    """能否看该来源的批次 / 映射 / 失败明细：``importer.batch:read`` 或来源自己的 ``view``。"""
    if perms.has(*_BATCH_READ):
        return True
    view = access_for(source).view
    return view is not None and perms.has(*view)


def visible_sources(perms: EffectivePermissions) -> frozenset[str] | None:
    """可见来源集合；``None`` = 全部来源（持 ``importer.batch:read``）。"""
    if perms.has(*_BATCH_READ):
        return None
    return frozenset(
        source
        for source, access in SOURCE_ACCESS.items()
        if access.view is not None and perms.has(*access.view)
    )


def _require(perms: EffectivePermissions, required: tuple[str, str], source: str) -> None:
    scope, action = required
    if not perms.has(scope, action):
        raise PermissionDeniedError(
            f"缺少权限 {scope}:{action}",
            details={"required_scope": scope, "required_action": action, "source": source},
        )


def require_write(perms: EffectivePermissions, source: str) -> None:
    """上传、重试该来源；不满足 → 403。"""
    _require(perms, access_for(source).write, source)


def require_mapping(perms: EffectivePermissions, source: str) -> None:
    """保存 / 重置该来源的字段映射；不满足 → 403。"""
    _require(perms, access_for(source).mapping, source)


def require_resolve(perms: EffectivePermissions, source: str) -> None:
    """裁决该来源的冲突（字段级权限另判）；不满足 → 403。"""
    _require(perms, access_for(source).resolve, source)


def require_view(perms: EffectivePermissions, source: str) -> None:
    """看该来源的映射等（不涉及具体批次时）；不满足 → 403。"""
    if not can_view(perms, source):
        view = access_for(source).view or _BATCH_READ
        _require(perms, view, source)


def describe_access(perms: EffectivePermissions) -> list[dict[str, Any]]:
    """每个已注册来源的能力，供前端决定显示哪些上传 / 映射 / 裁决按钮。"""
    items: list[dict[str, Any]] = []
    for source in sorted(ImportAdapterRegistry.sources()):
        access = access_for(source)
        items.append(
            {
                "source": source,
                "label": SOURCE_LABELS.get(source, source),
                # 重复规则可切换的来源（商品资料、博主），导入记录页据此显示新计数列（8a-6）
                "configurable": is_configurable(source),
                "can_view": can_view(perms, source),
                "can_upload": perms.has(*access.write),
                "can_map": perms.has(*access.mapping),
                "can_resolve": perms.has(*access.resolve),
            }
        )
    return items


__all__ = [
    "SCOPE_PRODUCT_IMPORT",
    "SOURCE_ACCESS",
    "SOURCE_LABELS",
    "SourceAccess",
    "access_for",
    "can_view",
    "describe_access",
    "require_mapping",
    "require_resolve",
    "require_view",
    "require_write",
    "visible_sources",
]
