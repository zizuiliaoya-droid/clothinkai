"""8a 商品资料线：运营可维护商品资料、商品资料导入的来源级权限（及后续 8a 各项的数据变更）。

## 为什么
业务方 10-07 定：跟单与运营都能改商品资料，靠审计留痕；运营可看可改成本价；商品资料导入
从 PR / 主管收回，交给跟单与运营（设计 §10、§4.6，补充二 Q4 / Q5）。

## 本迁移按区段组织
``upgrade()`` 依次调 ``_upgrade_style`` → ``_upgrade_import_tables`` → ``_upgrade_permissions``
→ ``_report``；``downgrade()`` 依次调 ``_downgrade_permissions`` → ``_downgrade_import_tables``
→ ``_downgrade_style``。各区段随 8a 的各项改动分步填写（设计 §24.1）：

- 权限（8a-7）：入册 ``product.import:write``（商品资料导入、映射、冲突裁决只认这一个 scope，
  跟单 / 运营靠 ``product.*:*``、管理员靠 ``*`` 命中；PR / 主管的角色数据不动，因此收回）；
  给 ``operations`` 授 ``product.*:*``（``permission`` 里已有这一行，跟单在用，仍兜底入册）
- 款式表（8a-1 / 8a-4）、导入表（8a-6）：见对应函数的说明

## 不删数据、不清汇总覆盖
upgrade 方向只加表、加列、放宽约束、插权限行：不删任何数据、不删列、不改任何外键；
汇总表的列与口径不变，**不清 ``report_summary_coverage``**（设计 §3.4）。
downgrade 撤回授权时只删运营那一条 ``product.*:*`` 的 ``role_permission``（``permission``
里的 ``product.*:*`` 跟单在用，不删）；删 ``product.import:write`` 之前先删引用它的
``user_permission_override``（外键 ``RESTRICT``，不先删会让 downgrade 中止；迁移用
BYPASSRLS 角色连库，FORCE RLS 挡不住这条 DELETE）。

Revision ID: 057_8a_goods_master_data
Revises: 056_goods_short_name
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "057_8a_goods_master_data"
down_revision: str | Sequence[str] | None = "056_goods_short_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 新入册的商品资料导入权限
_IMPORT_SCOPE = "product.import:write"
_IMPORT_SCOPE_NAME = "导入商品资料、配置其字段映射、处理其导入冲突"
# 运营获得与跟单相同的商品权限（兜底入册：库里本来就有，跟单在用）
_PRODUCT_ALL_SCOPE = "product.*:*"
_PRODUCT_ALL_NAME = "商品模块全部权限"
_GRANT_ROLE = "operations"
# downgrade 恢复 style.category NOT NULL 前给空类目补的值
_CATEGORY_FALLBACK = "未分类"
# import_batch 新增的五个计数列（8a-6，设计 §4.2）
_BATCH_COUNT_COLUMNS = ("filled", "skipped", "conflicted", "warning_count", "filled_objects")


def _log(msg: str) -> None:
    print(f"[057] {msg}")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def _upgrade_style(bind: Connection) -> None:
    """款式表：``style.category`` 放开 NOT NULL（8a-1）、加 ``external_image_url``（8a-4）。

    8a-1：款式表单不再有类目，接口新建的款式类目为 NULL；已有值不动（设计 §3.1）。
    """
    op.alter_column("style", "category", existing_type=sa.String(64), nullable=True)
    _log("style.category 放开 NOT NULL")
    # 8a-4：聚水潭「图片」列的外部链接，只存不取（设计 §7.4）
    op.add_column("style", sa.Column("external_image_url", sa.String(1024), nullable=True))
    _log("style 加 external_image_url")


def _drop_import_job_status_check(bind: Connection) -> None:
    """按定义查出 ``import_job`` 状态 CHECK 的实际名字再删（不按名字猜）。

    这条约束是 010 在 ``create_table`` 里建的，落库名取决于当时的命名约定
    （实测被套成 ``ck_import_job_ck_import_job_status``）；表上另一条 CHECK 是
    ``attempt_count >= 1``，不含 ``status``。恰好一条才删，否则中止迁移（设计 §3.2 第 4 步）。
    """
    names = (
        bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'import_job'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%status%'"
            )
        )
        .scalars()
        .all()
    )
    if len(names) != 1:
        raise RuntimeError(
            f"[057] import_job 上含 status 的 CHECK 应恰好一条，实际 {len(names)} 条："
            f"{', '.join(names) or '（无）'}；不猜着删，迁移中止"
        )
    # op.f：按库里的实际名删，不再套命名约定
    op.drop_constraint(op.f(names[0]), "import_job", type_="check")
    _log(f"删除 import_job 状态 CHECK：{names[0]}")


def _upgrade_import_tables(bind: Connection) -> None:
    """导入表：``import_batch`` 计数列、``import_job`` 状态与备注、``import_conflict`` 新表（8a-6）。

    - ``import_batch`` 加五个计数（行互斥的补空 / 跳过 / 冲突，与不互斥的带提示行数 / 补空对象数）
    - ``import_job.status`` 多 ``filled`` / ``skipped`` / ``conflict``；加 ``notes``（行提示与补空明细，
      只有字段名、没有值）
    - 新表 ``import_conflict``：同来源同对象只有一条待处理，由部分唯一索引保证（设计 §4.4）
    """
    for col in _BATCH_COUNT_COLUMNS:
        op.add_column(
            "import_batch",
            sa.Column(col, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.create_check_constraint(
        "ck_import_batch_8a_counts_nonneg",
        "import_batch",
        " AND ".join(f"{col} >= 0" for col in _BATCH_COUNT_COLUMNS),
    )
    _log(f"import_batch 加计数列：{', '.join(_BATCH_COUNT_COLUMNS)}")

    _drop_import_job_status_check(bind)
    op.create_check_constraint(
        "ck_import_job_status",
        "import_job",
        "status IN ('success','failed','filled','skipped','conflict')",
    )
    op.add_column("import_job", sa.Column("notes", postgresql.JSONB(), nullable=True))
    _log("import_job 状态 CHECK 改为五个取值，加 notes")

    op.create_table(
        "import_conflict",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "row_numbers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("object_type", sa.String(16), nullable=False),
        # 多态，不设外键：对象被删时冲突转「失效」（设计 §4.5）
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(128), nullable=False),
        sa.Column("object_label", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        # 不设外键，免得「先标旧冲突取代、再插新冲突」的插入顺序打架
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        # 批次被清理时冲突留痕仍在
        sa.ForeignKeyConstraint(["batch_id"], ["import_batch.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "object_type IN ('style','sku','goods','blogger')",
            name="ck_import_conflict_object_type",
        ),
        sa.CheckConstraint("kind IN ('fields','key')", name="ck_import_conflict_kind"),
        sa.CheckConstraint(
            "status IN ('pending','overwritten','kept','superseded','invalid')",
            name="ck_import_conflict_status",
        ),
        # 状态与留痕字段不能各写各的：待处理 ⇔ 没有处理时间
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_import_conflict_resolved_at",
        ),
    )
    # 同来源同对象只有一条待处理：批次之间可并发，只能由数据库保证
    op.create_index(
        "uq_import_conflict_pending",
        "import_conflict",
        ["tenant_id", "source", "object_type", "object_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_import_conflict_list",
        "import_conflict",
        ["tenant_id", "source", "status", "created_at"],
    )
    op.create_index("idx_import_conflict_batch", "import_conflict", ["tenant_id", "batch_id"])
    op.execute(enable_rls_sql("import_conflict"))
    _log("import_conflict 表已创建（RLS；部分唯一索引 uq_import_conflict_pending）")


def _upgrade_permissions(bind: Connection) -> None:
    """入册 ``product.import:write``；给 operations 授 ``product.*:*``。幂等。"""
    for scope, name in (
        (_IMPORT_SCOPE, _IMPORT_SCOPE_NAME),
        (_PRODUCT_ALL_SCOPE, _PRODUCT_ALL_NAME),
    ):
        res = bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
        _log(f"权限 {scope}：{'新入册' if res.rowcount else '已存在，跳过'}")

    res = bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT :id, r.id, p.id FROM role r, permission p "
            "WHERE r.code = :role_code AND p.scope = :scope "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"id": str(uuid4()), "role_code": _GRANT_ROLE, "scope": _PRODUCT_ALL_SCOPE},
    )
    _log(f"授予 {_GRANT_ROLE} 角色 {_PRODUCT_ALL_SCOPE}：{res.rowcount or 0} 项")


def _report(bind: Connection) -> None:
    """打印迁移后的权限状态（同 043 末尾）；后续步骤在这里追加各自的统计。"""
    null_category = bind.execute(
        sa.text("SELECT COUNT(*) FROM style WHERE category IS NULL")
    ).scalar_one()
    _log(f"类目为空的款式：{null_category} 个")

    scopes = (
        bind.execute(
            sa.text(
                "SELECT p.scope FROM role r "
                "JOIN role_permission rp ON rp.role_id = r.id "
                "JOIN permission p ON p.id = rp.permission_id "
                "WHERE r.code = :role_code AND p.scope LIKE 'product%' "
                "ORDER BY p.scope"
            ),
            {"role_code": _GRANT_ROLE},
        )
        .scalars()
        .all()
    )
    _log(f"{_GRANT_ROLE} 持有的 product 相关权限：{', '.join(scopes) or '（无）'}")

    holders = bind.execute(
        sa.text(
            """
            SELECT r.code, COUNT(u.id) AS users
            FROM role r
            JOIN role_permission rp ON rp.role_id = r.id
            JOIN permission p ON p.id = rp.permission_id
            LEFT JOIN user_role ur ON ur.role_id = r.id
            LEFT JOIN "user" u ON u.id = ur.user_id AND u.deleted_at IS NULL
            WHERE p.scope = :scope
            GROUP BY r.code
            ORDER BY r.code
            """
        ),
        {"scope": _PRODUCT_ALL_SCOPE},
    ).all()
    _log(f"持有 {_PRODUCT_ALL_SCOPE} 的角色与人数：")
    for code, users in holders:
        _log(f"  - {code}: {users} 人")


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_style(bind)
    _upgrade_import_tables(bind)
    _upgrade_permissions(bind)
    _report(bind)


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def _downgrade_permissions(bind: Connection) -> None:
    """撤回运营的 ``product.*:*``；删除 ``product.import:write``（先删引用它的个人权限覆盖）。"""
    res = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE "
            "role_id IN (SELECT id FROM role WHERE code = :role_code) AND "
            "permission_id IN (SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"role_code": _GRANT_ROLE, "scope": _PRODUCT_ALL_SCOPE},
    )
    _log(f"撤回 {_GRANT_ROLE} 角色 {_PRODUCT_ALL_SCOPE}：{res.rowcount or 0} 项")

    # user_permission_override.permission_id 是 ON DELETE RESTRICT：不先删会挡住下面删 permission
    res = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除 {res.rowcount or 0} 条个人权限覆盖（{_IMPORT_SCOPE}）")

    res = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除 {_IMPORT_SCOPE} 的角色授权 {res.rowcount or 0} 项")

    res = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = :scope"),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除权限 {_IMPORT_SCOPE}：{res.rowcount or 0} 项")


def _downgrade_import_tables(bind: Connection) -> None:
    """撤回导入表的变更（8a-6）。

    冲突记录随表一起丢掉（回退语义）；``filled`` / ``skipped`` / ``conflict`` 三类行本来就不是
    失败，回退成 ``success``；``import_batch`` 的五列直接删，PG 删列时连带删掉引用它们的多列
    CHECK，不依赖约束的实际名字（设计 §3.3 第 2-4 步）。
    """
    op.execute(disable_rls_sql("import_conflict"))
    op.drop_table("import_conflict")
    _log("删除 import_conflict 表")

    res = bind.execute(
        sa.text(
            "UPDATE import_job SET status = 'success' "
            "WHERE status IN ('filled','skipped','conflict')"
        )
    )
    _log(f"import_job 补空 / 跳过 / 冲突行回退为 success：{res.rowcount or 0} 行")
    _drop_import_job_status_check(bind)
    op.create_check_constraint(
        "ck_import_job_status", "import_job", "status IN ('success','failed')"
    )
    op.drop_column("import_job", "notes")
    _log("import_job 状态 CHECK 恢复两个取值，删 notes")

    for col in _BATCH_COUNT_COLUMNS:
        op.drop_column("import_batch", col)
    _log(f"import_batch 删计数列：{', '.join(_BATCH_COUNT_COLUMNS)}")


def _downgrade_style(bind: Connection) -> None:
    """撤回款式表的变更（8a-1 / 8a-4）。

    8a-4：删 ``external_image_url``（回退语义：外部图片链接随之丢弃）。
    8a-1：类目为空的款式先补「未分类」（056 及以前导入缺类目时写的就是它），再恢复 NOT NULL。
    """
    op.drop_column("style", "external_image_url")
    _log("style 删 external_image_url")
    res = bind.execute(
        sa.text("UPDATE style SET category = :fallback WHERE category IS NULL"),
        {"fallback": _CATEGORY_FALLBACK},
    )
    _log(f"类目为空的款式补「{_CATEGORY_FALLBACK}」：{res.rowcount or 0} 个")
    op.alter_column("style", "category", existing_type=sa.String(64), nullable=False)
    _log("style.category 恢复 NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    _downgrade_permissions(bind)
    _downgrade_import_tables(bind)
    _downgrade_style(bind)
