"""回填商品分层数据：由现有款式与千牛链接生成 goods_main / goods_style_item。

回填规则（按优先级，一个款式只会被归入一个商品）：

1. **套装**：``style.qianniu_product_id`` 被多个款式共用 → 这些款式归入同一个商品，
   ``is_suit=true``，``goods_title`` 按货号升序用 ``+`` 连接款名。
   这是用户手工填千牛ID 表达套装的方式。
2. **单件（有链接）**：``platform_product`` 里的千牛链接 → 每条链接的 ``style_id``
   生成一个单件商品。同一款挂多个千牛ID 时（生产有 8 例），每条链接各自成一个商品 ——
   它们在店铺里本来就是独立的售卖链接。
3. **单件（无链接）**：其余未归属的款式各自成一个商品，暂无链接。

``goods_code`` 取值：套装用 ``SUIT-<千牛ID>``，单件用款式货号；同款多链接时
第二条起追加 ``-<千牛ID>`` 以保证租户内唯一。

``single_goods_cost`` 取该款式启用 SKU 的成本价**最小非空值**。多 SKU 成本不一致时
以最低价入账，并在下方打印差异清单供人工核对 —— 这是需要业务确认的地方，
迁移不做猜测性加工。

``platform_product.goods_main_id`` 按 ``platform_id`` 回填；万相台链接沿用与千牛
相同的商品归属（万相台主体ID 就是淘宝商品ID）。

Revision ID: 037_backfill_goods
Revises: 036_goods_main_layer
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037_backfill_goods"
down_revision: str | Sequence[str] | None = "036_goods_main_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _log(msg: str) -> None:
    print(f"[037] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # 款式的单件货品成本：启用 SKU 的最小非空成本价。
    op.execute(
        """
        CREATE TEMP TABLE tmp_style_cost AS
        SELECT s.id AS style_id,
               s.tenant_id,
               s.style_code,
               s.style_name,
               s.qianniu_product_id,
               MIN(sk.cost_price) FILTER (
                 WHERE sk.cost_price IS NOT NULL AND sk.is_active AND NOT sk.is_deleted
               ) AS single_goods_cost,
               COUNT(DISTINCT sk.cost_price) FILTER (
                 WHERE sk.cost_price IS NOT NULL AND sk.is_active AND NOT sk.is_deleted
               ) AS distinct_cost_count
        FROM style s
        LEFT JOIN sku sk ON sk.style_id = s.id
        WHERE s.is_deleted = false
        GROUP BY s.id, s.tenant_id, s.style_code, s.style_name, s.qianniu_product_id
        """
    )

    conflicts = bind.execute(
        sa.text(
            "SELECT style_code, distinct_cost_count FROM tmp_style_cost "
            "WHERE distinct_cost_count > 1 ORDER BY style_code"
        )
    ).all()
    if conflicts:
        _log(f"以下 {len(conflicts)} 个款式的 SKU 成本价不一致，已取最低价，请人工核对：")
        for code, cnt in conflicts:
            _log(f"  - {code}: {cnt} 种不同成本价")
    else:
        _log("所有款式的 SKU 成本价一致（或仅一个值），无需人工核对")

    # ---------- 1. 套装：同一个 qianniu_product_id 被多个款式共用 ---------- #
    op.execute(
        """
        CREATE TEMP TABLE tmp_suit AS
        SELECT tenant_id, qianniu_product_id, COUNT(*) AS member_count
        FROM tmp_style_cost
        WHERE qianniu_product_id IS NOT NULL AND qianniu_product_id <> ''
        GROUP BY tenant_id, qianniu_product_id
        HAVING COUNT(*) > 1
        """
    )
    op.execute(
        """
        INSERT INTO goods_main (
            id, tenant_id, goods_code, goods_title, main_image_key,
            category, season, brand_id, is_suit, is_active, is_deleted,
            created_at, updated_at
        )
        SELECT gen_random_uuid(),
               t.tenant_id,
               'SUIT-' || t.qianniu_product_id,
               agg.title,
               agg.main_image_key,
               agg.category,
               agg.season,
               agg.brand_id,
               true, true, false, NOW(), NOW()
        FROM tmp_suit t
        JOIN LATERAL (
            SELECT string_agg(c.style_name, '+' ORDER BY c.style_code) AS title,
                   (array_agg(s.main_image_key ORDER BY c.style_code))[1] AS main_image_key,
                   (array_agg(s.category ORDER BY c.style_code))[1] AS category,
                   (array_agg(s.season ORDER BY c.style_code))[1] AS season,
                   (array_agg(s.brand_id ORDER BY c.style_code))[1] AS brand_id
            FROM tmp_style_cost c
            JOIN style s ON s.id = c.style_id
            WHERE c.tenant_id = t.tenant_id
              AND c.qianniu_product_id = t.qianniu_product_id
        ) agg ON true
        """
    )
    suit_count = bind.execute(sa.text("SELECT COUNT(*) FROM tmp_suit")).scalar_one()
    _log(f"套装商品：{suit_count} 个（同千牛ID 多款）")

    op.execute(
        """
        INSERT INTO goods_style_item (
            id, tenant_id, goods_main_id, style_id, single_goods_cost,
            sort_order, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.tenant_id, g.id, c.style_id, c.single_goods_cost,
               ROW_NUMBER() OVER (PARTITION BY g.id ORDER BY c.style_code) - 1,
               true, NOW(), NOW()
        FROM tmp_style_cost c
        JOIN tmp_suit t
          ON t.tenant_id = c.tenant_id AND t.qianniu_product_id = c.qianniu_product_id
        JOIN goods_main g
          ON g.tenant_id = c.tenant_id AND g.goods_code = 'SUIT-' || c.qianniu_product_id
        """
    )

    # 套装的千牛链接
    op.execute(
        """
        UPDATE platform_product pp
        SET goods_main_id = g.id
        FROM goods_main g
        WHERE g.tenant_id = pp.tenant_id
          AND g.goods_code = 'SUIT-' || pp.platform_id
          AND pp.goods_main_id IS NULL
        """
    )

    # ---------- 2. 单件（有千牛链接）---------- #
    # 同款多链接时每条链接各自成商品，goods_code 第二条起追加千牛ID 保唯一。
    op.execute(
        """
        CREATE TEMP TABLE tmp_link AS
        SELECT pp.id AS pp_id,
               pp.tenant_id,
               pp.platform_id,
               pp.style_id,
               c.style_code,
               c.style_name,
               c.single_goods_cost,
               ROW_NUMBER() OVER (
                 PARTITION BY pp.tenant_id, pp.style_id ORDER BY pp.platform_id
               ) AS seq
        FROM platform_product pp
        JOIN tmp_style_cost c ON c.style_id = pp.style_id
        WHERE pp.platform = '千牛'
          AND pp.goods_main_id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO goods_main (
            id, tenant_id, goods_code, goods_title, main_image_key,
            category, season, brand_id, is_suit, is_active, is_deleted,
            created_at, updated_at
        )
        SELECT gen_random_uuid(),
               l.tenant_id,
               CASE WHEN l.seq = 1 THEN l.style_code
                    ELSE l.style_code || '-' || l.platform_id END,
               l.style_name,
               s.main_image_key, s.category, s.season, s.brand_id,
               false, true, false, NOW(), NOW()
        FROM tmp_link l
        JOIN style s ON s.id = l.style_id
        """
    )
    op.execute(
        """
        INSERT INTO goods_style_item (
            id, tenant_id, goods_main_id, style_id, single_goods_cost,
            sort_order, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), l.tenant_id, g.id, l.style_id, l.single_goods_cost,
               0, true, NOW(), NOW()
        FROM tmp_link l
        JOIN goods_main g
          ON g.tenant_id = l.tenant_id
         AND g.goods_code = CASE WHEN l.seq = 1 THEN l.style_code
                                 ELSE l.style_code || '-' || l.platform_id END
        ON CONFLICT (tenant_id, goods_main_id, style_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE platform_product pp
        SET goods_main_id = g.id
        FROM tmp_link l
        JOIN goods_main g
          ON g.tenant_id = l.tenant_id
         AND g.goods_code = CASE WHEN l.seq = 1 THEN l.style_code
                                 ELSE l.style_code || '-' || l.platform_id END
        WHERE pp.id = l.pp_id
        """
    )
    link_count = bind.execute(sa.text("SELECT COUNT(*) FROM tmp_link")).scalar_one()
    _log(f"单件商品（由千牛链接生成）：{link_count} 个")

    # ---------- 3. 其余未归属款式各自成一个商品 ---------- #
    op.execute(
        """
        INSERT INTO goods_main (
            id, tenant_id, goods_code, goods_title, main_image_key,
            category, season, brand_id, is_suit, is_active, is_deleted,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), c.tenant_id, c.style_code, c.style_name,
               s.main_image_key, s.category, s.season, s.brand_id,
               false, s.is_active, false, NOW(), NOW()
        FROM tmp_style_cost c
        JOIN style s ON s.id = c.style_id
        WHERE NOT EXISTS (
            SELECT 1 FROM goods_style_item gsi WHERE gsi.style_id = c.style_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM goods_main g
            WHERE g.tenant_id = c.tenant_id AND g.goods_code = c.style_code
        )
        """
    )
    op.execute(
        """
        INSERT INTO goods_style_item (
            id, tenant_id, goods_main_id, style_id, single_goods_cost,
            sort_order, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.tenant_id, g.id, c.style_id, c.single_goods_cost,
               0, true, NOW(), NOW()
        FROM tmp_style_cost c
        JOIN goods_main g
          ON g.tenant_id = c.tenant_id AND g.goods_code = c.style_code
        WHERE NOT EXISTS (
            SELECT 1 FROM goods_style_item gsi WHERE gsi.style_id = c.style_id
        )
        ON CONFLICT (tenant_id, goods_main_id, style_id) DO NOTHING
        """
    )

    # ---------- 4. 万相台链接沿用同 platform_id 的商品归属 ---------- #
    op.execute(
        """
        UPDATE platform_product pp
        SET goods_main_id = src.goods_main_id
        FROM platform_product src
        WHERE src.tenant_id = pp.tenant_id
          AND src.platform = '千牛'
          AND src.platform_id = pp.platform_id
          AND src.goods_main_id IS NOT NULL
          AND pp.platform <> '千牛'
          AND pp.goods_main_id IS NULL
        """
    )

    total_goods = bind.execute(sa.text("SELECT COUNT(*) FROM goods_main")).scalar_one()
    total_items = bind.execute(sa.text("SELECT COUNT(*) FROM goods_style_item")).scalar_one()
    unmapped = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_product WHERE goods_main_id IS NULL")
    ).scalar_one()
    orphan_styles = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM style s WHERE s.is_deleted = false AND NOT EXISTS "
            "(SELECT 1 FROM goods_style_item g WHERE g.style_id = s.id)"
        )
    ).scalar_one()
    _log(f"合计：goods_main={total_goods}，goods_style_item={total_items}")
    _log(f"未归属商品的平台链接：{unmapped} 条（应为 0）")
    _log(f"未归属商品的在用款式：{orphan_styles} 个（应为 0）")

    op.execute("DROP TABLE IF EXISTS tmp_style_cost")
    op.execute("DROP TABLE IF EXISTS tmp_suit")
    op.execute("DROP TABLE IF EXISTS tmp_link")


def downgrade() -> None:
    # 回填产生的数据整体清掉；结构由 036 负责。
    op.execute("UPDATE platform_product SET goods_main_id = NULL")
    op.execute("DELETE FROM goods_style_item")
    op.execute("DELETE FROM goods_main")
