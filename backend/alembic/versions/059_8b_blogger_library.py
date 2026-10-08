"""8b 博主库：（平台, 账号）唯一、网页ID / 主页链接 / 平台统计 / 报价备注四列、标签字典维护权。

## 为什么
业务方 10-07 / 10-08 定：一个博主库、多平台，（平台 + 账号）唯一；抖音用「博主ID」当账号，
「网页ID」照存可搜；博主加主页链接；抖音「报价」多是文字，原文存进报价备注（字段权限与报价相同）；
标签字典由主管 + 管理员维护（设计 ``docs/requirements/20261008-博主库设计.md`` §4、§5）。

## upgrade（每步打印行数；不删数据）
1. 打印博主按平台 × 软删的分布、两列标签非空的博主数（只报告）
2. 平台为空的补「小红书」（10-08 生产预期 0 行）；平台不在枚举里的只打印
3. 预检新键：未删博主按（租户, 平台, 账号）重复 → 中止（防旧索引被手工删过）
4. 打印只差大小写 / 首尾空白的同号（按原样比较，不处理）
5. 先建新唯一索引 ``uq_blogger_platform_account``，再删旧的 ``uq_blogger_xiaohongshu_id``
   （任何时刻都有唯一约束）
6. 加四列（均可空、无默认，不重写表）
7. 入册 ``blogger_tag:write`` 并授 pr_manager（管理员靠 ``*``）。不种字典：存量博主没有标签

## downgrade
1. 预检旧键：未删博主同账号在多个平台都有 → 中止，不自动改博主
2. 建回旧索引、删新索引
3. 让旧代码裁决不了的待处理冲突失效（字段含新列，或来源是旧代码没注册的 ``huitun_douyin``）
4. M4：从 ``huitun_douyin`` 批次的 ``import_job.raw_data`` 去掉受保护列（微信号、报价）——
   旧代码没注册这个来源，失败明细一列都不遮，运营会看到达人微信与报价原文
5. 删四列（回退语义：数据随列丢弃）
6. 删 ``dict_item(blogger_tag)``（留着会在旧版商品字典接口里露出来）
7. 先删引用 ``blogger_tag:write`` 的个人权限覆盖（外键 RESTRICT），再删角色授权与权限

## 编号
流程线 PR-2 也占 059，后合并的那个改号（同时改日志前缀）。

Revision ID: 059_8b_blogger_library
Revises: 058_8a_goods_master_data
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision: str = "059_8b_blogger_library"
down_revision: str | Sequence[str] | None = "058_8a_goods_master_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_PLATFORM = "小红书"
_OLD_INDEX = "uq_blogger_xiaohongshu_id"
_NEW_INDEX = "uq_blogger_platform_account"
# 新增四列（名字, 类型）
_NEW_COLUMNS = (
    ("web_id", sa.String(64)),
    ("homepage_url", sa.String(1024)),
    ("platform_metrics", postgresql.JSONB()),
    ("quote_note", sa.String(500)),
)
# 下行时让待处理冲突失效的字段（不在旧版 BloggerApplier.specs 里，旧代码选覆盖会 KeyError）
_NEW_CONFLICT_FIELDS = ("web_id", "homepage_url", "quote_note")
# 灰豚抖音来源（旧代码没有注册）
_DOUYIN_SOURCE = "huitun_douyin"
# M4：抖音文件里受字段权限保护的源列（微信号 → wechat；报价 → quote / quote_note）。
# 迁移不 import app 代码，写成字面常量；tests/unit/test_douyin_constants.py 对 adapter 核对
_DOUYIN_PROTECTED_RAW_COLUMNS = ("微信号", "报价")
_INVALID_NOTE = "059 回退：冲突涉及已删除的博主字段或来源（灰豚抖音），已失效"
# 标签字典
_TAG_DICT_TYPE = "blogger_tag"
_TAG_SCOPE = "blogger_tag:write"
_TAG_SCOPE_NAME = "维护博主标签字典"
_TAG_GRANT_ROLES = ("pr_manager",)


def _log(msg: str) -> None:
    print(f"[059] {msg}")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def _report_bloggers(bind: Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT platform, is_deleted, count(*) AS n FROM blogger "
            "GROUP BY platform, is_deleted ORDER BY platform, is_deleted"
        )
    ).all()
    _log("博主按平台 × 软删：")
    for platform, is_deleted, n in rows:
        _log(f"  - {platform!r} / is_deleted={is_deleted}: {n}")
    if not rows:
        _log("  （无博主）")
    tagged = bind.execute(
        sa.text(
            "SELECT count(*) FILTER (WHERE category_tags <> '[]'::jsonb), "
            "count(*) FILTER (WHERE quality_tags <> '[]'::jsonb) FROM blogger"
        )
    ).one()
    _log(f"类目标签非空的博主 {tagged[0]} 个，质量标签非空的博主 {tagged[1]} 个")


def _fill_platform(bind: Connection) -> None:
    res = bind.execute(
        sa.text(
            "UPDATE blogger SET platform = CAST(:p AS text) "
            "WHERE platform IS NULL OR btrim(platform) = ''"
        ),
        {"p": _DEFAULT_PLATFORM},
    )
    _log(f"平台为空的博主补「{_DEFAULT_PLATFORM}」：{res.rowcount or 0} 行")

    # 只报告：取当前代码的枚举（得物等新平台随代码出现），不改已有的非小红书值
    from app.modules.blogger.enums import Platform

    known = [p.value for p in Platform]
    n = bind.execute(
        sa.text(
            "SELECT count(*) FROM blogger WHERE NOT (platform = ANY(CAST(:known AS text[])))"
        ),
        {"known": known},
    ).scalar_one()
    _log(f"平台不在枚举（{'、'.join(known)}）里的博主：{n} 行（不处理）")


def _precheck_new_key(bind: Connection) -> None:
    n = bind.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT 1 FROM blogger WHERE is_deleted = false "
            "  GROUP BY tenant_id, platform, xiaohongshu_id HAVING count(*) > 1"
            ") d"
        )
    ).scalar_one()
    if n:
        raise RuntimeError(
            f"[059] upgrade 中止：{n} 组未删除的博主（租户, 平台, 账号）重复，"
            f"新唯一索引建不起来；请先软删或改账号"
        )
    _log("新键（租户, 平台, 账号）预检：0 组重复")


def _report_case_variants(bind: Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT count(*) AS groups, COALESCE(sum(n), 0) AS total FROM ("
            "  SELECT count(*) AS n FROM blogger WHERE is_deleted = false "
            "  GROUP BY tenant_id, platform, lower(btrim(xiaohongshu_id)) "
            "  HAVING count(DISTINCT xiaohongshu_id) > 1"
            ") d"
        )
    ).one()
    _log(f"只差大小写 / 首尾空白的同号：{rows[0]} 组 {rows[1]} 行（按原样比较，不处理）")


def _upgrade_index(bind: Connection) -> None:
    op.execute(
        f"CREATE UNIQUE INDEX {_NEW_INDEX} ON blogger (tenant_id, platform, xiaohongshu_id) "
        "WHERE is_deleted = false"
    )
    _log(f"新建唯一索引 {_NEW_INDEX} (tenant_id, platform, xiaohongshu_id)")
    op.execute(f"DROP INDEX {_OLD_INDEX}")
    _log(f"删除旧唯一索引 {_OLD_INDEX}")


def _upgrade_columns(bind: Connection) -> None:
    for name, type_ in _NEW_COLUMNS:
        op.add_column("blogger", sa.Column(name, type_, nullable=True))
    _log(f"blogger 加列：{', '.join(n for n, _ in _NEW_COLUMNS)}")


def _upgrade_permissions(bind: Connection) -> None:
    res = bind.execute(
        sa.text(
            "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:scope AS text), CAST(:name AS text), "
            "'function', NOW(), NOW()) "
            "ON CONFLICT (scope) DO NOTHING"
        ),
        {"id": str(uuid4()), "scope": _TAG_SCOPE, "name": _TAG_SCOPE_NAME},
    )
    _log(f"权限 {_TAG_SCOPE}：{'新入册' if res.rowcount else '已存在，跳过'}")
    for role_code in _TAG_GRANT_ROLES:
        res = bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT gen_random_uuid(), r.id, p.id FROM role r, permission p "
                "WHERE r.code = CAST(:role_code AS text) AND p.scope = CAST(:scope AS text) "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"role_code": role_code, "scope": _TAG_SCOPE},
        )
        _log(f"授予 {role_code} 角色 {_TAG_SCOPE}：{res.rowcount or 0} 项")


def _report_permissions(bind: Connection) -> None:
    holders = bind.execute(
        sa.text(
            """
            SELECT r.code, COUNT(u.id) AS users
            FROM role r
            JOIN role_permission rp ON rp.role_id = r.id
            JOIN permission p ON p.id = rp.permission_id
            LEFT JOIN user_role ur ON ur.role_id = r.id
            LEFT JOIN "user" u ON u.id = ur.user_id AND u.deleted_at IS NULL
            WHERE p.scope = CAST(:scope AS text)
            GROUP BY r.code
            ORDER BY r.code
            """
        ),
        {"scope": _TAG_SCOPE},
    ).all()
    _log(f"持有 {_TAG_SCOPE} 的角色与人数（另有持 * 的管理员）：")
    for code, users in holders:
        _log(f"  - {code}: {users} 人")
    if not holders:
        _log("  （无）")


def upgrade() -> None:
    bind = op.get_bind()
    _report_bloggers(bind)
    _fill_platform(bind)
    _precheck_new_key(bind)
    _report_case_variants(bind)
    _upgrade_index(bind)
    _upgrade_columns(bind)
    _upgrade_permissions(bind)
    _report_permissions(bind)


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def _precheck_old_key(bind: Connection) -> None:
    n = bind.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT 1 FROM blogger WHERE is_deleted = false "
            "  GROUP BY tenant_id, xiaohongshu_id HAVING count(*) > 1"
            ") d"
        )
    ).scalar_one()
    if n:
        raise RuntimeError(
            f"[059] downgrade 中止：{n} 组账号在多个平台都有未删除的博主，"
            f"旧唯一索引建不起来；请先软删或改账号"
        )
    _log("旧键（租户, 账号）预检：0 组重复")


def _downgrade_index(bind: Connection) -> None:
    op.execute(
        f"CREATE UNIQUE INDEX {_OLD_INDEX} ON blogger (tenant_id, xiaohongshu_id) "
        "WHERE is_deleted = false"
    )
    _log(f"建回旧唯一索引 {_OLD_INDEX}")
    op.execute(f"DROP INDEX {_NEW_INDEX}")
    _log(f"删除唯一索引 {_NEW_INDEX}")


def _invalidate_conflicts(bind: Connection) -> None:
    """整条冲突失效，不拆字段（冲突一条一对象，拆开会和「整条取代」的语义打架）。必须在删列之前。"""
    res = bind.execute(
        sa.text(
            "UPDATE import_conflict "
            "SET status = 'invalid', resolved_at = NOW(), resolution_note = CAST(:note AS text) "
            "WHERE status = 'pending' AND object_type = 'blogger' "
            "AND (source = CAST(:source AS text) "
            "     OR EXISTS (SELECT 1 FROM jsonb_array_elements(fields) f "
            "                WHERE f->>'field' = ANY(CAST(:fields AS text[]))))"
        ),
        {
            "note": _INVALID_NOTE,
            "source": _DOUYIN_SOURCE,
            "fields": list(_NEW_CONFLICT_FIELDS),
        },
    )
    _log(f"待处理冲突失效（新字段或来源 {_DOUYIN_SOURCE}）：{res.rowcount or 0} 条")


def _strip_douyin_protected_raw(bind: Connection) -> None:
    """M4：旧代码遮不住的受保护列从原文里去掉（回退语义丢数据，与删列同理）。"""
    res = bind.execute(
        sa.text(
            "UPDATE import_job j SET raw_data = j.raw_data - CAST(:keys AS text[]) "
            "FROM import_batch b "
            "WHERE j.batch_id = b.id AND b.source = CAST(:source AS text) "
            "AND (j.raw_data - CAST(:keys AS text[])) <> j.raw_data"
        ),
        {"keys": list(_DOUYIN_PROTECTED_RAW_COLUMNS), "source": _DOUYIN_SOURCE},
    )
    _log(
        f"{_DOUYIN_SOURCE} 导入行原文去掉 {'、'.join(_DOUYIN_PROTECTED_RAW_COLUMNS)}："
        f"{res.rowcount or 0} 行"
    )


def _downgrade_columns(bind: Connection) -> None:
    for name, _ in _NEW_COLUMNS:
        n = bind.execute(
            sa.text(f"SELECT count(*) FROM blogger WHERE {name} IS NOT NULL")
        ).scalar_one()
        op.drop_column("blogger", name)
        _log(f"blogger 删列 {name}（非空 {n} 行随列丢弃）")


def _downgrade_tag_dict(bind: Connection) -> None:
    res = bind.execute(
        sa.text("DELETE FROM dict_item WHERE dict_type = CAST(:t AS text)"),
        {"t": _TAG_DICT_TYPE},
    )
    _log(f"删除博主标签字典 dict_item({_TAG_DICT_TYPE})：{res.rowcount or 0} 项")


def _downgrade_permissions(bind: Connection) -> None:
    # user_permission_override.permission_id 是 ON DELETE RESTRICT：不先删会挡住删 permission
    res = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = CAST(:scope AS text))"
        ),
        {"scope": _TAG_SCOPE},
    )
    _log(f"删除 {res.rowcount or 0} 条个人权限覆盖（{_TAG_SCOPE}）")
    res = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = CAST(:scope AS text))"
        ),
        {"scope": _TAG_SCOPE},
    )
    _log(f"删除 {_TAG_SCOPE} 的角色授权 {res.rowcount or 0} 项")
    res = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = CAST(:scope AS text)"),
        {"scope": _TAG_SCOPE},
    )
    _log(f"删除权限 {_TAG_SCOPE}：{res.rowcount or 0} 项")


def downgrade() -> None:
    bind = op.get_bind()
    _precheck_old_key(bind)
    _downgrade_index(bind)
    _invalidate_conflicts(bind)
    _strip_douyin_protected_raw(bind)
    _downgrade_columns(bind)
    _downgrade_tag_dict(bind)
    _downgrade_permissions(bind)
