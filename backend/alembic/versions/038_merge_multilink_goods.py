"""修正 037：同一款式的多条千牛链接归入同一个商品，而不是各自成商品。

037 把"一款多链接"拆成多个商品，理由是每条链接在店铺里都是独立售卖单元。
拿生产数据复核后这个判断是错的：

- 那 8 个多链接款式的 ``style_name`` 完全相同（例：``2025890 毛领棉服（蓝）``
  的两条链接分别卖 7879 与 270），是同一件衣服开了主链接 + 备用链接，
  不是两个不同商品。
- 更要紧的是 ``promotion``（站外推广费）与 ``order_adjustment``（刷单）原生挂在
  ``style`` 上。一个款式对应多个商品时，同一笔推广费会同时归进每个商品，
  报表里总花费翻倍、投产比虚低。
- PRD 改动 1 的原话就是"一个商品可以挂多条链接"（普通 + 直播），拆开反而偏离设计。

合并后 ``goods_main`` 与 ``style`` 在非套装场景是一对一，多条链接靠
``platform_product.channel`` 区分渠道；需要按渠道拆行时在报表层按 channel 分组，
不靠拆商品。

主商品的选取：``goods_code`` 等于款式货号的那个（037 第一条链接生成的就是它）；
若都不匹配则取 ``goods_code`` 最小的一个，保证可重复执行。

``downgrade`` 不还原拆分 —— 合并丢掉的是"哪条链接属于哪个派生商品"这个信息，
重建只会造出假数据。需要回到拆分状态就 downgrade 到 036 再重跑 037。

Revision ID: 038_merge_multilink
Revises: 037_backfill_goods
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038_merge_multilink"
down_revision: str | Sequence[str] | None = "037_backfill_goods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _log(msg: str) -> None:
    print(f"[038] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # 同一款式挂在多个非套装商品下 -> 排名第一的留作主商品，其余待合并。
    # 套装不参与：套装本来就该由多个款式共用一个商品。
    op.execute(
        """
        CREATE TEMP TABLE tmp_merge AS
        WITH dup_style AS (
            SELECT i.tenant_id, i.style_id
            FROM goods_style_item i
            JOIN goods_main g ON g.id = i.goods_main_id AND g.is_suit = false
            GROUP BY i.tenant_id, i.style_id
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT i.goods_main_id,
                   i.tenant_id,
                   i.style_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY i.tenant_id, i.style_id
                       ORDER BY (g.goods_code = s.style_code) DESC, g.goods_code
                   ) AS rn
            FROM goods_style_item i
            JOIN goods_main g ON g.id = i.goods_main_id AND g.is_suit = false
            JOIN style s ON s.id = i.style_id
            JOIN dup_style d
              ON d.tenant_id = i.tenant_id AND d.style_id = i.style_id
        )
        SELECT r.goods_main_id AS dup_goods_id,
               m.goods_main_id AS main_goods_id,
               r.tenant_id,
               r.style_id
        FROM ranked r
        JOIN ranked m
          ON m.tenant_id = r.tenant_id AND m.style_id = r.style_id AND m.rn = 1
        WHERE r.rn > 1
        """
    )

    # 防御：待合并商品若还挂着主商品没有的款式，合并会让该款式失去归属。
    # 037 的第二档每个商品只挂一个款式，这里不该命中；命中就说明数据形态
    # 超出预期，跳过这些商品并留下记录，宁可不合并也不丢关联。
    unexpected = bind.execute(
        sa.text(
            """
            SELECT DISTINCT g.goods_code
            FROM tmp_merge t
            JOIN goods_style_item i ON i.goods_main_id = t.dup_goods_id
            JOIN goods_main g ON g.id = t.dup_goods_id
            WHERE NOT EXISTS (
                SELECT 1 FROM goods_style_item mi
                WHERE mi.goods_main_id = t.main_goods_id
                  AND mi.style_id = i.style_id
            )
            ORDER BY g.goods_code
            """
        )
    ).scalars().all()
    if unexpected:
        _log(f"以下 {len(unexpected)} 个商品含主商品没有的款式，跳过合并，请人工处理：")
        for code in unexpected:
            _log(f"  - {code}")
        op.execute(
            """
            DELETE FROM tmp_merge t
            WHERE EXISTS (
                SELECT 1 FROM goods_style_item i
                WHERE i.goods_main_id = t.dup_goods_id
                  AND NOT EXISTS (
                      SELECT 1 FROM goods_style_item mi
                      WHERE mi.goods_main_id = t.main_goods_id
                        AND mi.style_id = i.style_id
                  )
            )
            """
        )

    merge_count = bind.execute(sa.text("SELECT COUNT(*) FROM tmp_merge")).scalar_one()
    if merge_count:
        detail = bind.execute(
            sa.text(
                """
                SELECT s.style_code,
                       (SELECT g.goods_code FROM goods_main g WHERE g.id = t.main_goods_id),
                       (SELECT g.goods_code FROM goods_main g WHERE g.id = t.dup_goods_id)
                FROM tmp_merge t
                JOIN style s ON s.id = t.style_id
                ORDER BY s.style_code
                """
            )
        ).all()
        _log(f"合并 {merge_count} 个派生商品到主商品：")
        for style_code, main_code, dup_code in detail:
            _log(f"  - {style_code}: {dup_code} -> {main_code}")
    else:
        _log("没有需要合并的商品")

    # 顺序要紧：platform_product.goods_main_id 是 RESTRICT 外键，
    # 必须先改指向再删商品。
    op.execute(
        """
        UPDATE platform_product pp
        SET goods_main_id = t.main_goods_id
        FROM tmp_merge t
        WHERE pp.goods_main_id = t.dup_goods_id
        """
    )
    op.execute(
        "DELETE FROM goods_style_item WHERE goods_main_id IN "
        "(SELECT dup_goods_id FROM tmp_merge)"
    )
    op.execute("DELETE FROM goods_main WHERE id IN (SELECT dup_goods_id FROM tmp_merge)")

    total_goods = bind.execute(sa.text("SELECT COUNT(*) FROM goods_main")).scalar_one()
    multi = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT i.style_id
                FROM goods_style_item i
                JOIN goods_main g ON g.id = i.goods_main_id AND g.is_suit = false
                GROUP BY i.tenant_id, i.style_id
                HAVING COUNT(*) > 1
            ) t
            """
        )
    ).scalar_one()
    unmapped = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_product WHERE goods_main_id IS NULL")
    ).scalar_one()
    orphan_styles = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM style s WHERE s.is_deleted = false AND NOT EXISTS "
            "(SELECT 1 FROM goods_style_item g WHERE g.style_id = s.id)"
        )
    ).scalar_one()
    _log(f"合计：goods_main={total_goods}")
    _log(f"仍属多个非套装商品的款式：{multi} 个（应为 0）")
    _log(f"未归属商品的平台链接：{unmapped} 条（应为 0）")
    _log(f"未归属商品的在用款式：{orphan_styles} 个（应为 0）")

    op.execute("DROP TABLE IF EXISTS tmp_merge")


def downgrade() -> None:
    # 合并是有损的：派生商品与具体链接的对应关系已经不存在，重建等于造数据。
    # 要回到 037 的拆分状态，downgrade 到 036 再重新 upgrade。
    pass
