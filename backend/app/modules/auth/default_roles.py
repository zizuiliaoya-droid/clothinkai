"""10 个预设角色 + 默认权限矩阵（应用设计 Q14=A 决策）。

启动时通过 Alembic data migration（003_u01_seed_initial_data.py）幂等同步到 DB。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.auth.permissions import SCOPE_ALL


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    description: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    is_system: bool = True


# ---------------------------------------------------------------------------
# 业务通用 scope（U01 阶段先占位，后续单元会真正定义这些 scope）
# 这里使用通配符 module.*:* 让默认角色矩阵能正常 seed
# ---------------------------------------------------------------------------

PRODUCT_ALL = "product.*:*"
PRODUCT_READ = "product.*:read"
DESIGN_ALL = "design.*:*"
DESIGN_READ = "design.*:read"
BLOGGER_ALL = "blogger.*:*"
BLOGGER_READ = "blogger.*:read"
PROMOTION_ALL = "promotion.*:*"
PROMOTION_READ = "promotion.*:read"
PROMOTION_REVIEW = "promotion.review:approve"
FINANCE_ALL = "finance.*:*"
FINANCE_READ = "finance.*:read"
FINANCE_REVIEW = "finance.settlement:approve"
FINANCE_PAY = "finance.settlement:pay"
REPORT_READ = "report.*:read"
WECOM_ALL = "wecom.*:*"
WECOM_BIND_WRITE = "wecom.bind:write"
WECOM_MESSAGE_READ = "wecom.message:read"
NOTIFICATION_READ = "notification:read"
IMPORTER_ALL = "importer.*:*"
IMPORTER_READ = "importer.*:read"
IMPORTER_BATCH_READ = "importer.batch:read"
IMPORTER_BATCH_WRITE = "importer.batch:write"
IMPORTER_MAPPING_WRITE = "importer.mapping:write"


# ---------------------------------------------------------------------------
# 10 个预设角色
# ---------------------------------------------------------------------------

DEFAULT_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        code="admin",
        name="管理员",
        description="系统超级管理员，拥有所有权限",
        permissions=(SCOPE_ALL,),
    ),
    RoleSpec(
        code="platform_admin",
        name="平台管理员",
        description="跨租户的超级管理员（不属于任何 tenant）",
        permissions=(SCOPE_ALL,),
    ),
    RoleSpec(
        code="designer",
        name="设计师",
        description="负责设计稿与面辅料填写",
        permissions=(
            DESIGN_ALL,
            PRODUCT_READ,
        ),
    ),
    RoleSpec(
        code="design_assistant",
        name="设计助理",
        description="负责面辅料补齐、核价信息填写",
        permissions=(
            DESIGN_ALL,
            "design.costing:write",
            PRODUCT_READ,
        ),
    ),
    RoleSpec(
        code="pattern_maker",
        name="版师",
        description="负责制版与放码",
        permissions=("design.pattern:read", "design.pattern:write"),
    ),
    RoleSpec(
        code="merchandiser",
        name="跟单",
        description="负责工艺录入、商品成本表、核价审批",
        permissions=(
            PRODUCT_ALL,
            "design.craft:write",
            "design.tag_price:write",
            "design.confirm_price:approve",
        ),
    ),
    RoleSpec(
        code="pr",
        name="PR",
        description="负责站外推广录入与博主维护",
        permissions=(
            PROMOTION_ALL,
            BLOGGER_ALL,
            "report.publish_progress:read",
            IMPORTER_BATCH_READ,
            IMPORTER_BATCH_WRITE,
            WECOM_BIND_WRITE,
            WECOM_MESSAGE_READ,
            NOTIFICATION_READ,
        ),
    ),
    RoleSpec(
        code="pr_manager",
        name="PR 主管",
        description="PR 全部权限 + 财务结款核查 + 增加结算项",
        permissions=(
            PROMOTION_ALL,
            BLOGGER_ALL,
            PROMOTION_REVIEW,
            FINANCE_REVIEW,
            "finance.settlement:read",
            "finance.settlement:write",
            "finance.settlement_extra_item:write",
            REPORT_READ,
            IMPORTER_BATCH_READ,
            IMPORTER_BATCH_WRITE,
            IMPORTER_MAPPING_WRITE,
            WECOM_BIND_WRITE,
            WECOM_MESSAGE_READ,
            NOTIFICATION_READ,
        ),
    ),
    RoleSpec(
        code="finance",
        name="财务",
        description="负责付款、拍单、刷单、余额核对",
        permissions=(
            FINANCE_PAY,
            "finance.settlement:read",
            "finance.order_adjustment:write",
            "finance.balance:write",
        ),
    ),
    RoleSpec(
        code="operations",
        name="运营",
        description="只读访问报表与店铺数据",
        permissions=(
            REPORT_READ,
            PROMOTION_READ,
            BLOGGER_READ,
            PRODUCT_READ,
            IMPORTER_READ,
            WECOM_MESSAGE_READ,
            NOTIFICATION_READ,
        ),
    ),
)


# ---------------------------------------------------------------------------
# 工具：内置 permission 全集（含通配符）
# ---------------------------------------------------------------------------


def all_builtin_permission_scopes() -> tuple[str, ...]:
    """汇总所有预设角色用到的 scope（用于 seed permission 表）。"""
    seen: set[str] = set()
    for role in DEFAULT_ROLES:
        seen.update(role.permissions)
    return tuple(sorted(seen))
