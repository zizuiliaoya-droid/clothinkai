"""推广单的合作模式转 typed 字段，补寄回运费与站外推广成本。

PRD V1.4 模块二的全部硬约束都建立在「合作模式」之上（寄拍样品成本恒为 0、置换博主
服务费恒为 0、三种模式审核通过后走三个不同出口）。但现在它根本不是字段 —— 值存在
``source_extra->>'合作方式'`` 里，枚举只定义在前端 ``PromotionListPage.tsx``，后端零校验。
于是 PRD 里反复强调的「后端必须做分支判断，不可只靠前端」一条都落不了地。

这个迁移把它提成 typed 列，并补上成本口径缺的两块：

- ``return_shipping_fee``：寄回运费。PRD 公式里是站外推广成本的第三项，之前无处可存。
- ``total_promo_cost``：站外推广成本 = 博主服务费 + 样品成本 + 寄回运费。

``total_promo_cost`` 用 **生成列**而不是在 service 里手动重算。PRD 要求「单据任意成本
字段变更实时重算」，而写入路径有三条（HTTP create/update、Excel 导入、迁移脚本），
手动重算迟早漏一条；生成列由数据库保证，漏不掉。

回填情况（生产实测）：5156 条推广里只有 2 条填了「合作方式」（都是送拍），其余 5154 条
为空且全部是「未发布」的历史导入数据，不会再流转。所以字段可空：不编造历史模式，
真要流转某条历史单时由 service 要求先补（补一次之后就锁死，见 service 的 update 拦截）。

Revision ID: 046_coop_mode
Revises: 045_drop_bundle
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "046_coop_mode"
down_revision: str | Sequence[str] | None = "045_drop_bundle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODES = ("寄拍", "送拍", "置换")
_EXTRA_KEY = "合作方式"


def _log(msg: str) -> None:
    print(f"[046] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("promotion", sa.Column("cooperation_mode", sa.String(8), nullable=True))
    op.add_column("promotion", sa.Column("return_shipping_fee", sa.Numeric(10, 2), nullable=True))

    # 生成列必须在它引用的列都存在之后再加。
    op.execute(
        """
        ALTER TABLE promotion ADD COLUMN total_promo_cost numeric(12, 2)
        GENERATED ALWAYS AS (
            quote_amount
            + COALESCE(cost_snapshot, 0)
            + COALESCE(return_shipping_fee, 0)
        ) STORED
        """
    )

    # 从 JSONB 回填，顺手把不认识的值挑出来报告（不猜，留空让业务补）
    unknown = bind.execute(
        sa.text(
            """
            SELECT source_extra->>:key AS v, COUNT(*) AS n
            FROM promotion
            WHERE source_extra->>:key IS NOT NULL
              AND source_extra->>:key <> ''
              AND source_extra->>:key <> ALL(:modes)
            GROUP BY 1
            """
        ),
        {"key": _EXTRA_KEY, "modes": list(_MODES)},
    ).all()
    for value, count in unknown:
        _log(f"⚠ 无法识别的合作方式 {value!r}（{count} 条），保持为空待业务补")

    filled = bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET cooperation_mode = source_extra->>:key
            WHERE source_extra->>:key = ANY(:modes)
            """
        ),
        {"key": _EXTRA_KEY, "modes": list(_MODES)},
    )
    _log(f"从 source_extra 回填 cooperation_mode {filled.rowcount or 0} 条")

    # 删掉 JSONB 里的副本：两处并存一定会不同步，而 typed 列从此是唯一事实来源。
    cleaned = bind.execute(
        sa.text("UPDATE promotion SET source_extra = source_extra - :key WHERE source_extra ? :key"),
        {"key": _EXTRA_KEY},
    )
    _log(f"清理 source_extra 里的「{_EXTRA_KEY}」副本 {cleaned.rowcount or 0} 条")

    op.create_check_constraint(
        "ck_promotion_cooperation_mode",
        "promotion",
        "cooperation_mode IS NULL OR cooperation_mode IN ('寄拍', '送拍', '置换')",
    )
    op.create_check_constraint(
        "ck_promotion_return_shipping_fee_nonneg",
        "promotion",
        "return_shipping_fee IS NULL OR return_shipping_fee >= 0",
    )
    op.create_index(
        "idx_promotion_cooperation_mode",
        "promotion",
        ["tenant_id", "cooperation_mode"],
        postgresql_where=sa.text("cooperation_mode IS NOT NULL"),
    )

    stats = bind.execute(
        sa.text(
            """
            SELECT COALESCE(cooperation_mode, '(空)') AS mode,
                   COUNT(*) AS n,
                   COALESCE(SUM(total_promo_cost), 0) AS total
            FROM promotion
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
    ).all()
    _log("合作模式分布与站外推广成本合计：")
    for mode, count, total in stats:
        _log(f"  - {mode}: {count} 条，合计 {total}")


def downgrade() -> None:
    bind = op.get_bind()

    # 把 typed 值写回 JSONB，再删列，保证回滚后前端仍能从 source_extra 读到
    bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET source_extra = source_extra || jsonb_build_object(:key, cooperation_mode)
            WHERE cooperation_mode IS NOT NULL
            """
        ),
        {"key": _EXTRA_KEY},
    )

    op.drop_index("idx_promotion_cooperation_mode", table_name="promotion")
    op.drop_constraint("ck_promotion_return_shipping_fee_nonneg", "promotion", type_="check")
    op.drop_constraint("ck_promotion_cooperation_mode", "promotion", type_="check")
    op.drop_column("promotion", "total_promo_cost")
    op.drop_column("promotion", "return_shipping_fee")
    op.drop_column("promotion", "cooperation_mode")
