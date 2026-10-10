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
# 运维视图：平台链接（千牛ID / 万相台主体ID 与商品、渠道的绑定）。
#
# 刻意用 ops. 开头而不是 product. —— has() 的前缀通配只看第一段，
# 挂在 product 下会被跟单/运营的 product.*:* 与 product.*:read 命中，挡不住人。
#
# 也刻意写成两条具体 scope 而不是 ops.platform_link:*：has() 的通配形式只支持
# 「第一段.*:action」，ops.platform_link:* 匹配不上 has("ops.platform_link", "read")，
# 授了等于没授。要用通配就得写 ops.*:*，那又把未来所有 ops.* 权限一并放开了。
OPS_PLATFORM_LINK_READ = "ops.platform_link:read"
OPS_PLATFORM_LINK_WRITE = "ops.platform_link:write"
# 谈款审核（PRD 模块一）。独立一级域 negotiation，同样刻意不挂在 promotion. 下 ——
# PR 持 promotion.*:*，叫 promotion.negotiation:approve 会让 PR 自动拿到主管的审核权。
# 也不给 PR negotiation.*:*，否则 negotiation.review:approve 又被通配命中。
NEGOTIATION_READ = "negotiation:read"
NEGOTIATION_WRITE = "negotiation:write"
NEGOTIATION_REVIEW = "negotiation.review:approve"
# 催发任务（PRD 改动 2）。这里两个一级域的分法是故意的：
#
# promotion.urge:* 挂在 promotion 下**正是想要的** —— 催发是 PR 的日常工作，
# PR 的 promotion.*:* 自动覆盖，运营的 promotion.*:read 自动拿到只读，
# 所以 pr / pr_manager / operations 都不需要显式授权。
#
# urge_config:* 必须独立 —— 叫 promotion.urge_config:write 的话 PR 会连
# 「催过 3 次提示主管」这个阈值一起改掉。改阈值是管理层的事。
URGE_READ = "promotion.urge:read"
URGE_WRITE = "promotion.urge:write"
URGE_CONFIG_READ = "urge_config:read"
URGE_CONFIG_WRITE = "urge_config:write"
# 复盘（PRD 改动 4）。write 由 PR 的 promotion.*:* 覆盖，不必显式授。
#
# confirm 显式给主管，但要清楚它拦不住 PR —— PR 的 promotion.*:* 会命中
# promotion.retro:confirm。真正的门槛是 service 层的
# RetroSelfConfirmForbiddenError（不能确认自己写的复盘），和既有的
# promotion.review:approve 同一个处境。
RETRO_WRITE = "promotion.retro:write"
RETRO_CONFIRM = "promotion.retro:confirm"
# 7a-1 款式下拉（款式列表 / 款式下的商品 / 颜色尺码）。PR 只给这一条窄 scope，
# 不给 product.*:read（会连带成本表、字典等整个 product 域）。
PRODUCT_STYLE_READ = "product.style:read"
# 8b-3 博主标签字典维护（主管 + 管理员；管理员靠 *）。独立一级域 blogger_tag，
# 刻意不叫 blogger.tag:write —— has() 的前缀通配只看第一段，PR / 主管的 blogger.*:*
# 会命中它，PR 就能改字典了。读字典用现有的 blogger:read。迁移 059 同步授予现存库。
BLOGGER_TAG_WRITE = "blogger_tag:write"
# 流程线 PR-2 发货（迁移 060）。独立一级域 promotion_ship，刻意不叫 promotion.ship:* ——
# PR / 主管的 promotion.*:* 会命中它：推送仓库要「管理员或 PR 主管确认，不是 PR」，
# 待打单阶段 PR 只读、不能回填。管理员靠 *。
PROMOTION_SHIP_PUSH = "promotion_ship:push"  # 纳入发货 / 确认推送仓库 / 撤回推送：主管
PROMOTION_SHIP_FILL = "promotion_ship:fill"  # 回填快递信息、改单号：仓库
PROMOTION_SHIP_EXPORT = "promotion_ship:export"  # 导出待打单（业务方要求单独授权）：仓库
# 旧回填 scope。060 起回填接口改挂 promotion_ship:fill，已停用，仅为回滚保留
PROMOTION_WAREHOUSE_WRITE_LEGACY = "promotion.warehouse:write"


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
            # 谈款：PR 能建、能改草稿、能提交，但没有审核权
            NEGOTIATION_READ,
            NEGOTIATION_WRITE,
            PRODUCT_STYLE_READ,
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
            BLOGGER_TAG_WRITE,
            PROMOTION_SHIP_PUSH,
            # 谈款：主管是唯一能审的角色
            NEGOTIATION_READ,
            NEGOTIATION_WRITE,
            NEGOTIATION_REVIEW,
            PRODUCT_STYLE_READ,
            # 催发任务本身由 promotion.*:* 覆盖；阈值配置是独立域，要显式给
            URGE_CONFIG_READ,
            URGE_CONFIG_WRITE,
            RETRO_CONFIRM,
            FINANCE_REVIEW,
            "finance.settlement:read",
            "finance.settlement:write",
            "finance.settlement_extra_item:write",
            # 结款页 API 实际使用的作用域（migration 029 同步授予现存库）
            "settlement:read",
            "settlement:write",
            "settlement.review:approve",
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
            # 谈款：PRD 要求财务只读查看
            NEGOTIATION_READ,
            # 催发进度只读：财务要知道一单为什么迟迟没发出来
            URGE_READ,
            # 结款页 API 实际使用的作用域（migration 029 同步授予现存库）
            "settlement:read",
            "settlement:write",
            "settlement.review:approve",
            "settlement.pay:upload_proof",
        ),
    ),
    RoleSpec(
        code="operations",
        name="运营",
        description="看报表与店铺数据，维护平台链接与商品资料（商品 / 套装 / 款式 / 成本表）",
        permissions=(
            REPORT_READ,
            PROMOTION_READ,
            BLOGGER_READ,
            PRODUCT_READ,
            # 8a-7：与跟单相同的商品权限（商品 / 套装 / 款式 / SKU / 字典 / 商品资料导入）；
            # PRODUCT_READ 保留（058 之前的库里已有这一行，迁移只加不删）
            PRODUCT_ALL,
            IMPORTER_READ,
            WECOM_MESSAGE_READ,
            NOTIFICATION_READ,
            # 平台链接是运维职责：千牛ID 绑错款式会让整条销售数据算到别的商品上
            OPS_PLATFORM_LINK_READ,
            OPS_PLATFORM_LINK_WRITE,
            NEGOTIATION_READ,
        ),
    ),
    RoleSpec(
        code="warehouse",
        name="仓库",
        description="仓库打单：仅能查看待打单推广单并回传发货单号",
        permissions=(
            # 060 起仓库只走专用接口（/api/warehouse/shipments 与回填），收回 promotion:read：
            # 不收回的话仓库直接调推广列表仍能看到博主、发布链接等矩阵里「隐」的字段
            PROMOTION_SHIP_FILL,
            PROMOTION_SHIP_EXPORT,
            PROMOTION_WAREHOUSE_WRITE_LEGACY,
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
