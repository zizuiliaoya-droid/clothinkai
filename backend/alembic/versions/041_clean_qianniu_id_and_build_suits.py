"""清掉款式千牛ID 的 Excel 前导单引号，并把共用同一千牛ID 的款式合成套装。

037 按「多个款式共用同一个 qianniu_product_id」识别套装，但生产上这批数据被
Excel 的文本格式毁掉了：导出时数字列前面会补一个单引号强制转文本，导入后
``260419`` 的千牛ID 存成 ``'1074568657697``，与 ``260415`` 的 ``1074568657697``
字符串不相等，套装识别整个落空。

本迁移做两件事：

1. 清掉 ``style.qianniu_product_id`` 的前后单引号与空白（通用规则，不只针对这一例）。
2. 清理后重跑套装识别：同一个千牛ID 被多个款式共用 → 建 ``SUIT-<千牛ID>`` 商品，
   把成员款式挂进去，并把该千牛ID 的销售链接改指向套装。

第 2 步之后成员款式原来的单品商品可能变成空壳（链接已转走、款式已进套装）。
这种商品会被删掉，避免商品列表里堆无意义条目。但**只删确实没人用的**：仍有平台
链接指向它、或者成员款式没进套装的，一律保留 —— 生产上 ``260419`` 就有自己的
独立链接 ``1064105286010``（它既单卖又进套装），那个单品商品必须留着。

套装的单件货品成本取成员款式启用 SKU 的最小非空成本价，与 037 同口径。生产这两款
目前都没有 SKU，所以成本是空的，会在日志里提示。

Revision ID: 041_clean_qn_suits
Revises: 040_styles_from_qianniu
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "041_clean_qn_suits"
down_revision: str | Sequence[str] | None = "040_styles_from_qianniu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _log(msg: str) -> None:
    print(f"[041] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1. 清掉 Excel 文本格式留下的单引号 ---------- #
    dirty = bind.execute(
        sa.text(
            """
            SELECT style_code, qianniu_product_id
            FROM style
            WHERE qianniu_product_id IS NOT NULL
              AND btrim(qianniu_product_id, ' ''') <> qianniu_product_id
            ORDER BY style_code
            """
        )
    ).all()
    if dirty:
        _log(f"清理 {len(dirty)} 个款式的千牛ID（去前后单引号与空白）：")
        for style_code, raw in dirty:
            _log(f"  - {style_code}: {raw!r} -> {raw.strip().strip(chr(39))!r}")
        op.execute(
            """
            UPDATE style
            SET qianniu_product_id = NULLIF(btrim(qianniu_product_id, ' '''), ''),
                updated_at = NOW()
            WHERE qianniu_product_id IS NOT NULL
              AND btrim(qianniu_product_id, ' ''') <> qianniu_product_id
            """
        )
    else:
        _log("没有需要清理的千牛ID")

    # ---------- 2. 清理后重跑套装识别 ---------- #
    op.execute(
        """
        CREATE TEMP TABLE tmp_suit_group AS
        SELECT s.tenant_id,
               s.qianniu_product_id,
               COUNT(*) AS member_count,
               string_agg(s.style_name, '+' ORDER BY s.style_code) AS title,
               string_agg(s.style_code, ',' ORDER BY s.style_code) AS codes
        FROM style s
        WHERE s.is_deleted = false
          AND COALESCE(s.qianniu_product_id, '') <> ''
        GROUP BY s.tenant_id, s.qianniu_product_id
        HAVING COUNT(*) > 1
        """
    )
    groups = bind.execute(
        sa.text(
            "SELECT qianniu_product_id, member_count, codes, title "
            "FROM tmp_suit_group ORDER BY qianniu_product_id"
        )
    ).all()
    if not groups:
        _log("没有共用千牛ID 的款式组，无需建套装")
        op.execute("DROP TABLE IF EXISTS tmp_suit_group")
        return

    _log(f"识别出 {len(groups)} 个套装组：")
    for qn_id, member_count, codes, title in groups:
        _log(f"  - SUIT-{qn_id}: {member_count} 款 [{codes}] {title}")

    # 已经存在同名套装就跳过（迁移可重复执行）
    op.execute(
        """
        INSERT INTO goods_main (
            id, tenant_id, goods_code, goods_title, main_image_key,
            category, season, brand_id, is_suit, is_active, is_deleted,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), t.tenant_id,
               'SUIT-' || t.qianniu_product_id,
               t.title,
               agg.main_image_key, agg.category, agg.season, agg.brand_id,
               true, true, false, NOW(), NOW()
        FROM tmp_suit_group t
        JOIN LATERAL (
            SELECT (array_agg(s.main_image_key ORDER BY s.style_code))[1] AS main_image_key,
                   (array_agg(s.category ORDER BY s.style_code))[1] AS category,
                   (array_agg(s.season ORDER BY s.style_code))[1] AS season,
                   (array_agg(s.brand_id ORDER BY s.style_code))[1] AS brand_id
            FROM style s
            WHERE s.tenant_id = t.tenant_id
              AND s.is_deleted = false
              AND s.qianniu_product_id = t.qianniu_product_id
        ) agg ON true
        ON CONFLICT (tenant_id, goods_code) DO NOTHING
        """
    )

    # 成员款式挂进套装；单件成本取该款启用 SKU 的最小非空成本价（与 037 同口径）
    op.execute(
        """
        INSERT INTO goods_style_item (
            id, tenant_id, goods_main_id, style_id, single_goods_cost,
            sort_order, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), s.tenant_id, g.id, s.id,
               (
                 SELECT MIN(sk.cost_price) FROM sku sk
                 WHERE sk.style_id = s.id AND sk.cost_price IS NOT NULL
                   AND sk.is_active = true AND sk.is_deleted = false
               ),
               ROW_NUMBER() OVER (PARTITION BY g.id ORDER BY s.style_code) - 1,
               true, NOW(), NOW()
        FROM tmp_suit_group t
        JOIN style s
          ON s.tenant_id = t.tenant_id
         AND s.is_deleted = false
         AND s.qianniu_product_id = t.qianniu_product_id
        JOIN goods_main g
          ON g.tenant_id = t.tenant_id
         AND g.goods_code = 'SUIT-' || t.qianniu_product_id
        ON CONFLICT (tenant_id, goods_main_id, style_id) DO NOTHING
        """
    )

    # 该千牛ID 的销售链接改指向套装
    moved = bind.execute(
        sa.text(
            """
            UPDATE platform_product pp
            SET goods_main_id = g.id, updated_at = NOW()
            FROM tmp_suit_group t
            JOIN goods_main g
              ON g.tenant_id = t.tenant_id
             AND g.goods_code = 'SUIT-' || t.qianniu_product_id
            WHERE pp.tenant_id = t.tenant_id
              AND pp.platform_id = t.qianniu_product_id
              AND (pp.goods_main_id IS NULL OR pp.goods_main_id <> g.id)
            RETURNING pp.platform, pp.platform_id
            """
        )
    ).all()
    if moved:
        _log(f"改指向套装的链接 {len(moved)} 条：")
        for platform, platform_id in moved:
            _log(f"  - {platform} {platform_id}")

    # ---------- 3. 清掉变成空壳的单品商品 ---------- #
    # 只删同时满足：非套装、没有任何平台链接指向它、它的每个成员款式都已进入某个套装。
    # 生产上 260419 有自己的独立链接，所以它的单品商品会被保留。
    shells = bind.execute(
        sa.text(
            """
            SELECT g.id, g.goods_code
            FROM goods_main g
            WHERE g.is_suit = false
              AND NOT EXISTS (
                SELECT 1 FROM platform_product pp WHERE pp.goods_main_id = g.id
              )
              AND EXISTS (
                SELECT 1 FROM goods_style_item i WHERE i.goods_main_id = g.id
              )
              AND NOT EXISTS (
                SELECT 1 FROM goods_style_item i
                WHERE i.goods_main_id = g.id
                  AND NOT EXISTS (
                    SELECT 1 FROM goods_style_item si
                    JOIN goods_main sg ON sg.id = si.goods_main_id AND sg.is_suit = true
                    WHERE si.style_id = i.style_id
                  )
              )
            ORDER BY g.goods_code
            """
        )
    ).all()
    if shells:
        _log(f"删掉 {len(shells)} 个空壳单品商品（链接已转走、款式已进套装）：")
        for _gid, goods_code in shells:
            _log(f"  - {goods_code}")
        ids = [gid for gid, _ in shells]
        op.execute(
            sa.text("DELETE FROM goods_style_item WHERE goods_main_id = ANY(:ids)").bindparams(
                sa.bindparam("ids", value=ids, type_=sa.ARRAY(sa.dialects.postgresql.UUID))
            )
        )
        op.execute(
            sa.text("DELETE FROM goods_main WHERE id = ANY(:ids)").bindparams(
                sa.bindparam("ids", value=ids, type_=sa.ARRAY(sa.dialects.postgresql.UUID))
            )
        )
    else:
        _log("没有需要清理的空壳商品")

    # ---------- 4. 报告 ---------- #
    suit_cost = bind.execute(
        sa.text(
            """
            SELECT g.goods_code,
                   COUNT(i.id) AS members,
                   COUNT(i.single_goods_cost) AS with_cost,
                   COALESCE(SUM(i.single_goods_cost), 0) AS total_cost
            FROM goods_main g
            JOIN goods_style_item i ON i.goods_main_id = g.id AND i.is_active = true
            WHERE g.is_suit = true
            GROUP BY g.goods_code
            ORDER BY g.goods_code
            """
        )
    ).all()
    for goods_code, members, with_cost, total_cost in suit_cost:
        note = "" if with_cost == members else f"（{members - with_cost} 款缺成本价，需人工补 SKU）"
        _log(f"套装成本 {goods_code}: {members} 款，合计 {total_cost}{note}")

    totals = bind.execute(
        sa.text(
            "SELECT COUNT(*) FILTER (WHERE is_suit), COUNT(*) FROM goods_main"
        )
    ).one()
    unmapped = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_product WHERE goods_main_id IS NULL")
    ).scalar_one()
    orphan = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM style s WHERE s.is_deleted = false AND NOT EXISTS "
            "(SELECT 1 FROM goods_style_item g WHERE g.style_id = s.id)"
        )
    ).scalar_one()
    multi_nonsuit = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT i.style_id FROM goods_style_item i
                JOIN goods_main g ON g.id = i.goods_main_id AND g.is_suit = false
                GROUP BY i.tenant_id, i.style_id HAVING COUNT(*) > 1
            ) t
            """
        )
    ).scalar_one()
    _log(f"合计：goods_main={totals[1]}，其中套装 {totals[0]} 个")
    _log(f"未归属商品的平台链接：{unmapped} 条（应为 0）")
    _log(f"未归属商品的在用款式：{orphan} 个（应为 0）")
    _log(f"仍属多个非套装商品的款式：{multi_nonsuit} 个（应为 0）")

    op.execute("DROP TABLE IF EXISTS tmp_suit_group")


def downgrade() -> None:
    # 把套装拆回单品：链接改指向成员款式各自的单品商品（没有就重建），再删套装。
    # 千牛ID 的单引号不还原 —— 那是脏数据，回滚也没有恢复的价值。
    bind = op.get_bind()
    suits = bind.execute(
        sa.text("SELECT id, goods_code FROM goods_main WHERE is_suit = true ORDER BY goods_code")
    ).all()
    for suit_id, goods_code in suits:
        members = bind.execute(
            sa.text(
                """
                SELECT s.id, s.tenant_id, s.style_code, s.style_name, s.category,
                       s.season, s.brand_id, s.main_image_key, s.is_active
                FROM goods_style_item i
                JOIN style s ON s.id = i.style_id
                WHERE i.goods_main_id = :g
                ORDER BY i.sort_order
                """
            ),
            {"g": suit_id},
        ).all()
        if not members:
            continue
        first = members[0]
        # 成员款式的单品商品：存在就复用，不存在就按货号重建
        solo_id = bind.execute(
            sa.text("SELECT id FROM goods_main WHERE tenant_id = :t AND goods_code = :c"),
            {"t": first[1], "c": first[2]},
        ).scalar_one_or_none()
        if solo_id is None:
            solo_id = bind.execute(
                sa.text(
                    """
                    INSERT INTO goods_main (
                        id, tenant_id, goods_code, goods_title, main_image_key,
                        category, season, brand_id, is_suit, is_active, is_deleted,
                        created_at, updated_at
                    )
                    VALUES (
                        gen_random_uuid(), :t, :c, :title, :img, :cat, :season, :brand,
                        false, :active, false, NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "t": first[1],
                    "c": first[2],
                    "title": first[3],
                    "img": first[7],
                    "cat": first[4],
                    "season": first[5],
                    "brand": first[6],
                    "active": first[8],
                },
            ).scalar_one()
            bind.execute(
                sa.text(
                    """
                    INSERT INTO goods_style_item (
                        id, tenant_id, goods_main_id, style_id, sort_order, is_active,
                        created_at, updated_at
                    )
                    VALUES (gen_random_uuid(), :t, :g, :s, 0, true, NOW(), NOW())
                    ON CONFLICT (tenant_id, goods_main_id, style_id) DO NOTHING
                    """
                ),
                {"t": first[1], "g": solo_id, "s": first[0]},
            )
        bind.execute(
            sa.text(
                "UPDATE platform_product SET goods_main_id = :solo WHERE goods_main_id = :suit"
            ),
            {"solo": solo_id, "suit": suit_id},
        )
        bind.execute(
            sa.text("DELETE FROM goods_style_item WHERE goods_main_id = :g"), {"g": suit_id}
        )
        bind.execute(sa.text("DELETE FROM goods_main WHERE id = :g"), {"g": suit_id})
        print(f"[041] downgrade: 拆回 {goods_code} -> {first[2]}")
