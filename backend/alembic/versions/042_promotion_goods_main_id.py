"""推广记录加商品归属字段，把推广费归商品的口径从查询时现算改为落库。

报表按商品聚合后，推广费要从款式归到商品。原来是在查询时用 LATERAL 现算
「主商品」（``ORDER BY gg.is_suit, gg.goods_code LIMIT 1``，非套装优先、货号次之）。
这个做法有两个问题：

1. 归属按货号字典序挑，一旦给某个款式新建一个编码更小的商品，历史推广费的归属会
   突然跳走，上个月的报表数字跟着变。
2. 系统猜不出「这笔推广是为套装做的还是为单品做的」，只有 PR 知道，而算出来的归属
   没法人工纠正。

字段可空：报表 SQL 用 ``COALESCE(p.goods_main_id, <原 LATERAL>)``，历史数据或异常
情况自动回落到旧规则，不会漏算。

回填沿用现有规则，所以上线后报表数字一个都不变 —— 这次只是把归属固化下来，
让它可查、可改、不再漂移。业务要调整某笔推广的归属，在界面上改即可。

Revision ID: 042_promo_goods
Revises: 041_clean_qn_suits
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "042_promo_goods"
down_revision: str | Sequence[str] | None = "041_clean_qn_suits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _log(msg: str) -> None:
    print(f"[042] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "promotion",
        sa.Column("goods_main_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_promotion_goods_main",
        "promotion",
        "goods_main",
        ["goods_main_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_promotion_goods",
        "promotion",
        ["tenant_id", "goods_main_id", "publish_status"],
    )

    # 回填：与报表现有的「主商品优先」规则逐字一致，保证数字不变。
    # 用相关子查询而不是 UPDATE ... FROM LATERAL —— LATERAL 只能引用 FROM 列表里
    # 排在它前面的项，引用不到 UPDATE 的目标表。
    op.execute(
        """
        UPDATE promotion p
        SET goods_main_id = (
            SELECT gi.goods_main_id
            FROM goods_style_item gi
            JOIN goods_main gg ON gg.id = gi.goods_main_id
            WHERE gi.style_id = p.style_id AND gi.is_active = true
            ORDER BY gg.is_suit, gg.goods_code
            LIMIT 1
        )
        WHERE p.goods_main_id IS NULL
        """
    )

    total, filled = bind.execute(
        sa.text(
            "SELECT COUNT(*), COUNT(goods_main_id) FROM promotion"
        )
    ).one()
    _log(f"回填：{filled}/{total} 条推广记录已确定商品归属")
    if filled < total:
        missing = bind.execute(
            sa.text(
                """
                SELECT p.style_code_snapshot, COUNT(*)
                FROM promotion p
                WHERE p.goods_main_id IS NULL
                GROUP BY p.style_code_snapshot
                ORDER BY COUNT(*) DESC
                LIMIT 20
                """
            )
        ).all()
        _log(f"以下款式没有对应商品，归属留空（报表会回落到兜底规则）：")
        for style_code, cnt in missing:
            _log(f"  - {style_code}: {cnt} 条")

    # 款式同时属于多个商品的，归属是「猜」出来的，列出来供业务核对。
    ambiguous = bind.execute(
        sa.text(
            """
            SELECT p.style_code_snapshot,
                   (SELECT g.goods_code FROM goods_main g WHERE g.id = p.goods_main_id) AS assigned,
                   (
                     SELECT string_agg(g2.goods_code, ', ' ORDER BY g2.is_suit, g2.goods_code)
                     FROM goods_style_item gi2
                     JOIN goods_main g2 ON g2.id = gi2.goods_main_id
                     WHERE gi2.style_id = p.style_id AND gi2.is_active = true
                   ) AS candidates,
                   COUNT(*) AS promo_count,
                   SUM(p.quote_amount) FILTER (WHERE p.publish_status = '已发布'
                                                 AND p.is_active = true) AS published_spend
            FROM promotion p
            WHERE (
                SELECT COUNT(*) FROM goods_style_item gi
                WHERE gi.style_id = p.style_id AND gi.is_active = true
            ) > 1
            GROUP BY p.style_code_snapshot, p.goods_main_id, p.style_id
            ORDER BY p.style_code_snapshot
            """
        )
    ).all()
    if ambiguous:
        _log(f"以下 {len(ambiguous)} 个款式同属多个商品，归属按「非套装优先」推定，请人工核对：")
        for style_code, assigned, candidates, promo_count, published_spend in ambiguous:
            spend = published_spend or 0
            _log(
                f"  - {style_code}: {promo_count} 条推广（已发布 {spend}）"
                f" 归入 {assigned}，候选 [{candidates}]"
            )
    else:
        _log("没有同属多个商品的款式，归属无歧义")


def downgrade() -> None:
    op.drop_index("idx_promotion_goods", table_name="promotion")
    op.drop_constraint("fk_promotion_goods_main", "promotion", type_="foreignkey")
    op.drop_column("promotion", "goods_main_id")
