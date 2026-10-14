"""流程线 M4：结款链 + 召回闭环列 + R33 运费付款单空表（流程线设计 2.5）。
- ``promotion`` 加 4 列（都可空）：结款聊天截图、已结款时间、召回寄回单号截图（都 → attachment RESTRICT）、
  运费承担方（CHECK 博主垫付 / 到付）。召回两列与空表 ``return_freight_payment`` 一起建，PR-8 才用。
- ``settlement`` 加 ``payment_adjust_note``：实付 ≠ 应付时的调整说明。「不一致必须写」只在 service 对新录入强制，
  不加 CHECK：生产存量 5 张已付款结款单里有 3 张实付 ≠ 应付（或为空），加 CHECK 会让迁移失败。
- 存量（按条件写，不按 id）：推广单已付款 → 已结款（不补聊天截图、不回填 ``settled_at``）；
  结款单已驳回、推广单还停在待付款的 → 结款驳回（预期 0 行，让 PR 能走重提）。结款单表一行不改。
- 权限：``promotion_review:approve``（推广审核 / R33 付款单审核）授 pr_manager，管理员靠 ``*``；独立一级域：
  旧的 ``promotion.review:approve`` 会被 PR 的 ``promotion.*:*`` 命中。旧 scope 不删（下行要用）。
  字段 scope ``field.promotion.payment_qr:read|write``、``field.settlement.payment_adjust_note:read|write`` 只入册（照 012）。
下行有损：已结款 → 已付款（应付 = 0 直接结清的也折回已付款），结款驳回 → 待付款；删新表、5 列；
这 5 个 scope 上的个人授权（``user_permission_override``）一并删除。已驳回的结款单留在已驳回。
Revision ID: 065_settlement_flow
Revises: 064_urge_complete
Create Date: 2026-10-13
"""
from __future__ import annotations
from typing import Any, Sequence
from uuid import uuid4
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from app.core.security.rls import disable_rls_sql, enable_rls_sql
revision: str = "065_settlement_flow"
down_revision: str | Sequence[str] | None = "064_urge_complete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# 迁移编号只出现在这里（与并行分支谁后合并谁改号，见 brief-flow6 第 1 节）
_REV = "065"
_PAID = "已付款"
_SETTLED = "已结款"
_PENDING_PAY = "待付款"
_SETTLE_REJECTED = "结款驳回"
_SETTLEMENT_REJECTED = "已驳回"
_PAYERS = ("博主垫付", "到付")
_FREIGHT_STATUSES = ("待审核", "待付款", "已付款", "已驳回")
_APP_ROLE = "clothing_app"
_PROMOTION_NEW_COLUMNS = (
    "settlement_chat_attachment_id",
    "settled_at",
    "recall_waybill_attachment_id",
    "return_shipping_payer",
)
_PERMISSIONS: list[tuple[str, str]] = [
    ("promotion_review:approve", "推广审核通过 / 驳回、寄回运费付款单审核"),
]
_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["promotion_review:approve"],
}
# 照 012：只入册、不绑角色（默认按字段注册表的角色判定，这些 scope 供个人授予 / 撤销引用）
_FIELD_SCOPES: list[tuple[str, str]] = [
    ("field.promotion.payment_qr:read", "字段-推广收款码-读"),
    ("field.promotion.payment_qr:write", "字段-推广收款码-写"),
    ("field.settlement.payment_adjust_note:read", "字段-结算调整说明-读"),
    ("field.settlement.payment_adjust_note:write", "字段-结算调整说明-写"),
]
def _log(msg: str) -> None:
    print(f"[{_REV}] {msg}")
def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)
def _base_columns() -> list[sa.Column[Any]]:
    """TenantScopedModel 的 4 列。"""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]
def _distribution(bind: sa.Connection) -> None:
    promo = bind.execute(
        sa.text(
            "SELECT settlement_status, count(*) AS n FROM promotion "
            "GROUP BY settlement_status ORDER BY settlement_status"
        )
    ).all()
    _log("推广单结款状态：" + ("、".join(f"{r.settlement_status} {r.n}" for r in promo) or "无"))
    settle = bind.execute(
        sa.text(
            "SELECT settlement_status, count(*) AS n, coalesce(sum(total_amount), 0) AS total, "
            "count(*) FILTER (WHERE payment_amount IS DISTINCT FROM total_amount) AS ne "
            "FROM settlement GROUP BY settlement_status ORDER BY settlement_status"
        )
    ).all()
    _log(
        "结款单："
        + (
            "、".join(
                f"{r.settlement_status} {r.n} 张（合计 {r.total}，实付 ≠ 应付 {r.ne}）"
                for r in settle
            )
            or "无"
        )
    )
def _add_columns() -> None:
    def attachment_fk(name: str) -> sa.Column[Any]:
        return sa.Column(
            name,
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attachment.id", ondelete="RESTRICT"),
            nullable=True,
        )
    op.add_column("promotion", attachment_fk("settlement_chat_attachment_id"))
    op.add_column("promotion", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("promotion", attachment_fk("recall_waybill_attachment_id"))
    op.add_column("promotion", sa.Column("return_shipping_payer", sa.String(8), nullable=True))
    op.create_check_constraint(
        "ck_promotion_return_shipping_payer",
        "promotion",
        f"return_shipping_payer IS NULL OR return_shipping_payer IN ({_in_list(_PAYERS)})",
    )
    _log("promotion 加 4 列（结款聊天截图、已结款时间、召回寄回截图、运费承担方 + CHECK）")
    op.add_column("settlement", sa.Column("payment_adjust_note", sa.Text(), nullable=True))
    _log("settlement 加 payment_adjust_note（不加 CHECK，「不一致必须写」只在 service）")
def _create_return_freight_payment() -> None:
    def user_fk(name: str) -> sa.Column[Any]:
        return sa.Column(
            name,
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        )
    op.create_table(
        "return_freight_payment",
        *_base_columns(),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "status",
            sa.String(8),
            nullable=False,
            server_default=sa.text(f"'{_FREIGHT_STATUSES[0]}'"),
        ),
        # 提交时的收款码 / 寄回截图快照：之后推广单换码不影响这张单
        sa.Column("payment_qr_attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("waybill_attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        user_fk("submitted_by"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        user_fk("reviewed_by"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        user_fk("rejected_by"),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("payment_proof_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        user_fk("paid_by"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_qr_attachment_id"], ["attachment.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["waybill_attachment_id"], ["attachment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_proof_attachment_id"], ["attachment.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("amount > 0", name="ck_return_freight_amount_pos"),
        sa.CheckConstraint(
            f"status IN ({_in_list(_FREIGHT_STATUSES)})", name="ck_return_freight_status"
        ),
        sa.CheckConstraint(
            "status <> '已付款' OR "
            "(payment_date IS NOT NULL AND payment_proof_attachment_id IS NOT NULL)",
            name="ck_return_freight_paid_fields",
        ),
    )
    # 一张推广单最多一张，驳回后改同一张重提
    op.create_index(
        "uq_return_freight_promotion",
        "return_freight_payment",
        ["tenant_id", "promotion_id"],
        unique=True,
    )
    op.create_index(
        "idx_return_freight_status",
        "return_freight_payment",
        ["tenant_id", "status", sa.text("submitted_at DESC")],
    )
    op.execute(enable_rls_sql("return_freight_payment"))
    # 显式授权（照 063 对 line_event）：测试库与 CI 以 postgres 跑迁移，025 的默认权限（FOR ROLE clothing_bypass）
    # 不生效，表上没有任何授权，clothing_app 读写都被拒；生产属主是 clothing_bypass，默认权限会给到 arwd。
    # 设计 3.5 T1 ~ T6 只要 SELECT / INSERT / UPDATE（驳回后改同一张重提，不删），两边都收成这三项。
    # 主键是 uuid，没有序列要授。下行删表即可。
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON return_freight_payment TO {_APP_ROLE}")
    op.execute(f"REVOKE DELETE ON return_freight_payment FROM {_APP_ROLE}")
    _log(
        "return_freight_payment 空表已创建（RLS、uq_return_freight_promotion、idx_return_freight_status；"
        "clothing_app 只有 SELECT / INSERT / UPDATE）"
    )
def _migrate_statuses(bind: sa.Connection) -> None:
    settled = bind.execute(
        sa.text(
            "UPDATE promotion SET settlement_status = CAST(:to AS varchar) "
            "WHERE settlement_status = CAST(:from_ AS varchar)"
        ),
        {"to": _SETTLED, "from_": _PAID},
    )
    _log(f"推广单 已付款 → 已结款 {settled.rowcount or 0} 张（不补聊天截图、settled_at 不回填）")
    rejected = bind.execute(
        sa.text(
            "UPDATE promotion p SET settlement_status = CAST(:to AS varchar) FROM settlement s "
            "WHERE s.promotion_id = p.id AND s.tenant_id = p.tenant_id "
            "AND p.settlement_status = CAST(:pending AS varchar) "
            "AND s.settlement_status = CAST(:rejected AS varchar)"
        ),
        {"to": _SETTLE_REJECTED, "pending": _PENDING_PAY, "rejected": _SETTLEMENT_REJECTED},
    )
    _log(
        f"结款单已驳回、推广单待付款 → 结款驳回 {rejected.rowcount or 0} 张（预期 0）；结款单表不改"
    )
def _seed_permissions(bind: sa.Connection) -> None:
    for scope, name in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
    for scope, name in _FIELD_SCOPES:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'field', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
    granted = 0
    for role_code, scopes in _MATRIX.items():
        for scope in scopes:
            res = bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )
            granted += res.rowcount or 0
    _log(
        f"权限入册 {len(_PERMISSIONS)} 项功能 scope + {len(_FIELD_SCOPES)} 项字段 scope，"
        f"角色授权 {granted} 条（管理员靠 *）"
    )
    for role_code, scopes in _MATRIX.items():
        _log(f"  - {role_code}: {', '.join(scopes)}")
def upgrade() -> None:
    bind = op.get_bind()
    _distribution(bind)
    _add_columns()
    _create_return_freight_payment()
    _migrate_statuses(bind)
    _seed_permissions(bind)
def _log_lossy(bind: sa.Connection) -> None:
    row = bind.execute(
        sa.text(
            """
            SELECT
              count(*) FILTER (WHERE settlement_chat_attachment_id IS NOT NULL) AS chat,
              count(*) FILTER (WHERE settled_at IS NOT NULL) AS settled_at,
              count(*) FILTER (WHERE recall_waybill_attachment_id IS NOT NULL) AS waybill,
              count(*) FILTER (WHERE return_shipping_payer IS NOT NULL) AS payer
            FROM promotion
            """
        )
    ).one()
    notes = bind.execute(
        sa.text("SELECT count(*) FROM settlement WHERE payment_adjust_note IS NOT NULL")
    ).scalar_one()
    freight = bind.execute(sa.text("SELECT count(*) FROM return_freight_payment")).scalar_one()
    _log(
        f"下行有损：结款聊天截图 {row.chat} 张、已结款时间 {row.settled_at} 张、"
        f"召回寄回截图 {row.waybill} 张、运费承担方 {row.payer} 张、调整说明 {notes} 张、"
        f"运费付款单 {freight} 张将丢弃（附件本身仍在附件表）"
    )
def _fold_statuses(bind: sa.Connection) -> None:
    for to_status, from_status in ((_PAID, _SETTLED), (_PENDING_PAY, _SETTLE_REJECTED)):
        res = bind.execute(
            sa.text(
                "UPDATE promotion SET settlement_status = CAST(:to AS varchar) "
                "WHERE settlement_status = CAST(:from_ AS varchar)"
            ),
            {"to": to_status, "from_": from_status},
        )
        _log(f"推广单 {from_status} → {to_status} {res.rowcount or 0} 张")
def downgrade() -> None:
    bind = op.get_bind()
    _log_lossy(bind)
    _fold_statuses(bind)
    op.execute(disable_rls_sql("return_freight_payment"))
    op.drop_table("return_freight_payment")
    op.drop_column("settlement", "payment_adjust_note")
    op.drop_constraint("ck_promotion_return_shipping_payer", "promotion", type_="check")
    for column in _PROMOTION_NEW_COLUMNS:
        op.drop_column("promotion", column)
    _log("已删 return_freight_payment、promotion 4 列与 settlement.payment_adjust_note")
    # user_permission_override → permission 是 RESTRICT：先删个人授权，再删角色授权与 scope
    scopes = [s for s, _ in _PERMISSIONS] + [s for s, _ in _FIELD_SCOPES]
    overrides = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    _log(f"删除这 {len(scopes)} 个 scope 上的个人授权 {overrides.rowcount or 0} 条")
    grants = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
    _log(f"删除角色授权 {grants.rowcount or 0} 条与 {len(scopes)} 个 scope")
