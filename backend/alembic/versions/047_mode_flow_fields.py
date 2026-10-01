"""寄回单号转 typed 字段，驳回原因分类入库。

两个字段都是流转的前提，不能待在 JSONB 里：

- ``return_waybill``（博主寄回衣服单号）：PRD 模块二的硬约束之一 —— 寄拍模式审核通过后
  **必须先上传这个单号**才允许流转到待财务付款，没有单号财务看不到单据。
  后端要据此拦截，所以它必须是能被 service 读到并校验的字段，而不是
  ``source_extra->>'寄回单号'`` 里一个谁都能随便改的自由文本。

- ``review_reason_category``（驳回原因分类）：PRD 改动 5 要求主管驳回时三选一必填
  （延迟发文 / 流量差补发 / 衣服未寄回）。原来只有自由文本 ``review_reason``，
  分类统计不出来，也没法按原因做后续动作。

生产现状：5156 条推广里 ``source_extra->>'寄回单号'`` 全部为空，所以这次回填实际是 0 条。
保留回填逻辑是为了本地与测试环境里手工填过的数据不丢。

Revision ID: 047_mode_flow
Revises: 046_coop_mode
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "047_mode_flow"
down_revision: str | Sequence[str] | None = "046_coop_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WAYBILL_KEY = "寄回单号"
_REJECT_CATEGORIES = ("延迟发文", "流量差补发", "衣服未寄回")


def _log(msg: str) -> None:
    print(f"[047] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("promotion", sa.Column("return_waybill", sa.String(128), nullable=True))
    op.add_column(
        "promotion", sa.Column("review_reason_category", sa.String(16), nullable=True)
    )

    filled = bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET return_waybill = NULLIF(TRIM(source_extra->>:key), '')
            WHERE NULLIF(TRIM(source_extra->>:key), '') IS NOT NULL
            """
        ),
        {"key": _WAYBILL_KEY},
    )
    _log(f"从 source_extra 回填 return_waybill {filled.rowcount or 0} 条")

    cleaned = bind.execute(
        sa.text(
            "UPDATE promotion SET source_extra = source_extra - :key WHERE source_extra ? :key"
        ),
        {"key": _WAYBILL_KEY},
    )
    _log(f"清理 source_extra 里的「{_WAYBILL_KEY}」副本 {cleaned.rowcount or 0} 条")

    op.create_check_constraint(
        "ck_promotion_review_reason_category",
        "promotion",
        "review_reason_category IS NULL OR review_reason_category IN "
        "('延迟发文', '流量差补发', '衣服未寄回')",
    )
    # 寄拍审核时要查「这单有没有单号」，按模式+单号筛的场景会走这个索引
    op.create_index(
        "idx_promotion_return_waybill",
        "promotion",
        ["tenant_id", "return_waybill"],
        postgresql_where=sa.text("return_waybill IS NOT NULL"),
    )

    _log(f"驳回原因分类可选值：{', '.join(_REJECT_CATEGORIES)}")


def downgrade() -> None:
    bind = op.get_bind()

    # 把单号写回 JSONB，保证回滚后前端仍能从 source_extra 读到
    bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET source_extra = source_extra || jsonb_build_object(:key, return_waybill)
            WHERE return_waybill IS NOT NULL
            """
        ),
        {"key": _WAYBILL_KEY},
    )

    op.drop_index("idx_promotion_return_waybill", table_name="promotion")
    op.drop_constraint("ck_promotion_review_reason_category", "promotion", type_="check")
    op.drop_column("promotion", "review_reason_category")
    op.drop_column("promotion", "return_waybill")
