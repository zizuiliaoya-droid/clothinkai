"""为千牛日报里有销售但系统未建档的款式建最小档案，并补齐链接与商品归属。

039 补完能自动匹配的链接后，仍有 69 条千牛日报（¥69,020.50）进不了投产报表，
六成半是因为款式根本没在系统里建档 —— 补链接的前提是款式存在。

本迁移从千牛日报的 ``extra`` 反向建档：货号取「货号」，款名取「商品名称」，
上下架状态取「商品状态」。类目按款名推断（``category`` 是 NOT NULL，不能留空），
季节留空由人工补。每个新款式同时建一个非套装商品与对应的千牛/万相台链接，
建完即可进报表。

建出来的是**最小档案**：没有 SKU、没有成本价、没有季节、没有主图，类目是推断的。
``remark`` 里标注了来源与待补字段，人工在款式管理里补齐即可。这是权衡后的选择 ——
留着不建档，这批销售额就一直不进报表。

类目推断分两段：先在款名**前 20 字**里找品类词，找不到再看整条款名。淘宝标题末尾
习惯堆关键词（「...收腰衬衫女长袖花边拼接显瘦通勤上衣外套」结尾是"上衣外套"但主体
是衬衫），只看整条会把衬衫判成外套。

Revision ID: 040_styles_from_qianniu
Revises: 039_backfill_links
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "040_styles_from_qianniu"
down_revision: str | Sequence[str] | None = "039_backfill_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 「套装」是商品形态而不是品类，标题里出现就算，不看位置 —— 否则
# 「...水洗牛仔短外套女秋季显瘦古早外套半身裙套装」会被开头的「外套」抢先判成外套。
_SUIT_KEYWORDS = ("套装",)

# 其余类目从具体到宽泛，顺序即优先级：T恤 先于 上衣，裤/裙 先于 外套/上衣。
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("连衣裙", ("连衣裙",)),
    ("T恤", ("T恤", "体恤")),
    ("裤装", ("裤",)),
    ("裙装", ("裙",)),
    ("外套", ("外套", "夹克", "风衣", "棉服", "皮衣", "大衣", "羊羔毛", "羽绒")),
    ("上衣", ("上衣", "衬衫", "毛衣", "针织", "卫衣", "开衫", "背心", "吊带", "马甲", "罩衫")),
]
_FALLBACK_CATEGORY = "未分类"
_HEAD_CHARS = 20


def _log(msg: str) -> None:
    print(f"[040] {msg}")


def _guess_category(name: str) -> tuple[str, str]:
    """返回（类目, 判定依据）。套装全名优先，其余先看款名开头再看全名。"""
    for keyword in _SUIT_KEYWORDS:
        if keyword in name:
            return "套装", f"全名命中「{keyword}」"
    head = name[:_HEAD_CHARS]
    for scope, label in ((head, "开头"), (name, "全名")):
        for category, keywords in _CATEGORY_RULES:
            for keyword in keywords:
                if keyword in scope:
                    return category, f"{label}命中「{keyword}」"
    return _FALLBACK_CATEGORY, "无品类词"


# 未映射且货号能用：日报没有显式链接、没有千牛链接占用该 platform_id、
# 货号非空且不是千牛导出的占位符 -，且系统里确实没有对应款式。
_CANDIDATES = """
    SELECT q.tenant_id,
           q.extra->>'货号' AS style_code,
           MAX(q.extra->>'商品名称') AS style_name,
           MAX(q.extra->>'商品状态') AS shop_status,
           SUM(q.pay_amount) AS pay_amount,
           ARRAY_AGG(DISTINCT q.platform_id_snapshot) AS platform_ids
    FROM qianniu_daily q
    WHERE q.platform_product_id IS NULL
      AND COALESCE(q.platform_id_snapshot, '') <> ''
      AND COALESCE(q.extra->>'货号', '-') <> '-'
      AND COALESCE(q.extra->>'商品名称', '') <> ''
      AND NOT EXISTS (
        SELECT 1 FROM platform_product pp
        WHERE pp.tenant_id = q.tenant_id
          AND pp.platform = '千牛'
          AND pp.platform_id = q.platform_id_snapshot
      )
      AND NOT EXISTS (
        SELECT 1 FROM style s
        WHERE s.tenant_id = q.tenant_id
          AND s.is_deleted = false
          AND (
            q.extra->>'货号' = s.style_code
            OR (
              length(q.extra->>'货号') > length(s.style_code)
              AND length(q.extra->>'货号') - length(s.style_code) <= 4
              AND right(q.extra->>'货号', length(s.style_code)) = s.style_code
              AND left(
                    q.extra->>'货号',
                    length(q.extra->>'货号') - length(s.style_code)
                  ) ~ '^[A-Za-z]+$'
            )
          )
      )
    GROUP BY q.tenant_id, q.extra->>'货号'
    ORDER BY q.extra->>'货号'
"""


def upgrade() -> None:
    bind = op.get_bind()
    candidates = bind.execute(sa.text(_CANDIDATES)).all()
    if not candidates:
        _log("没有需要建档的款式")
        return

    brands: dict[UUID, UUID | None] = {}
    created_styles = 0
    created_goods = 0
    created_qn = 0
    created_ad = 0
    report: list[tuple[str, str, str, str]] = []
    prefix_warnings: list[tuple[str, str]] = []

    for tenant_id, style_code, style_name, shop_status, pay_amount, platform_ids in candidates:
        if tenant_id not in brands:
            brands[tenant_id] = bind.execute(
                sa.text(
                    "SELECT id FROM brand WHERE tenant_id = :t AND is_active = true "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"t": tenant_id},
            ).scalar_one_or_none()
        brand_id = brands[tenant_id]

        name = (style_name or style_code)[:255]
        category, reason = _guess_category(name)
        is_active = (shop_status or "") != "已下架"

        # 已有款式货号以新货号开头（系统里存在 2026038内里 / 2026039外套 这类
        # 混了部件名的货号），提示人工确认是否重复建档。
        similar = bind.execute(
            sa.text(
                "SELECT style_code FROM style WHERE tenant_id = :t AND is_deleted = false "
                "AND style_code LIKE :p ORDER BY style_code LIMIT 3"
            ),
            {"t": tenant_id, "p": f"{style_code}%"},
        ).scalars().all()
        if similar:
            prefix_warnings.append((style_code, ", ".join(similar)))

        style_id = uuid4()
        bind.execute(
            sa.text(
                """
                INSERT INTO style (
                    id, tenant_id, style_code, style_name, brand_id, category, season,
                    tags, tag_color, design_status, is_active, is_deleted, remark,
                    created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :style_code, :style_name, :brand_id, :category, NULL,
                    '[]'::jsonb, '[]'::jsonb, '大货', :is_active, false, :remark,
                    NOW(), NOW()
                )
                """
            ),
            {
                "id": style_id,
                "tenant_id": tenant_id,
                "style_code": style_code,
                "style_name": name,
                "brand_id": brand_id,
                "category": category,
                "is_active": is_active,
                "remark": (
                    "档案由千牛日报自动建立（migration 040）。"
                    f"类目「{category}」按款名推断（{reason}），"
                    "季节、SKU、成本价、主图待人工补齐。"
                ),
            },
        )
        created_styles += 1
        report.append((style_code, category, reason, str(pay_amount or 0)))

        # 每个新款式一个非套装商品，与 037/038 回填后的形态一致。
        goods_id = uuid4()
        # 用 ON CONFLICT 而不是 INSERT ... SELECT ... WHERE NOT EXISTS：后者会让同一个
        # 字符串参数在 SELECT 与 WHERE 两处被分别推断成 text 与 varchar，asyncpg 报
        # AmbiguousParameterError。ON CONFLICT 下每个参数只出现一次。
        inserted = bind.execute(
            sa.text(
                """
                INSERT INTO goods_main (
                    id, tenant_id, goods_code, goods_title, category, brand_id,
                    is_suit, is_active, is_deleted, created_at, updated_at
                )
                VALUES (
                    :id, :tenant_id, :goods_code, :goods_title, :category, :brand_id,
                    false, :is_active, false, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, goods_code) DO NOTHING
                """
            ),
            {
                "id": goods_id,
                "tenant_id": tenant_id,
                "goods_code": style_code,
                "goods_title": name,
                "category": category,
                "brand_id": brand_id,
                "is_active": is_active,
            },
        )
        if inserted.rowcount:
            created_goods += 1
        else:
            goods_id = bind.execute(
                sa.text(
                    "SELECT id FROM goods_main WHERE tenant_id = :t AND goods_code = :c"
                ),
                {"t": tenant_id, "c": style_code},
            ).scalar_one()
        bind.execute(
            sa.text(
                """
                INSERT INTO goods_style_item (
                    id, tenant_id, goods_main_id, style_id, sort_order, is_active,
                    created_at, updated_at
                )
                VALUES (:id, :tenant_id, :goods_main_id, :style_id, 0, true, NOW(), NOW())
                ON CONFLICT (tenant_id, goods_main_id, style_id) DO NOTHING
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "goods_main_id": goods_id,
                "style_id": style_id,
            },
        )

        # 同一货号可能有多条千牛链接（生产有 240627 一款两链接），逐条建。
        for platform_id in platform_ids:
            res = bind.execute(
                sa.text(
                    """
                    INSERT INTO platform_product (
                        id, tenant_id, platform, platform_id, style_id, title,
                        goods_main_id, channel, is_active, created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, '千牛', :platform_id, :style_id, :title,
                        :goods_main_id, '普通', true, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, platform, platform_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": tenant_id,
                    "platform_id": platform_id,
                    "style_id": style_id,
                    "title": name,
                    "goods_main_id": goods_id,
                },
            )
            created_qn += res.rowcount or 0

    # 万相台沿用同 platform_id 的千牛归属（主体ID 就是淘宝商品ID）。
    ad_res = bind.execute(
        sa.text(
            """
            INSERT INTO platform_product (
                id, tenant_id, platform, platform_id, style_id, title,
                goods_main_id, channel, is_active, created_at, updated_at
            )
            SELECT gen_random_uuid(), t.tenant_id, '万相台', t.platform_id, t.style_id,
                   t.title, t.goods_main_id, '普通', true, NOW(), NOW()
            FROM (
                SELECT DISTINCT a.tenant_id, a.platform_id_snapshot AS platform_id,
                       src.style_id, src.goods_main_id, src.title
                FROM ad_daily a
                JOIN platform_product src
                  ON src.tenant_id = a.tenant_id
                 AND src.platform = '千牛'
                 AND src.platform_id = a.platform_id_snapshot
                WHERE a.platform_product_id IS NULL
                  AND COALESCE(a.platform_id_snapshot, '') <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM platform_product pp
                    WHERE pp.tenant_id = a.tenant_id
                      AND pp.platform = '万相台'
                      AND pp.platform_id = a.platform_id_snapshot
                  )
            ) t
            """
        )
    )
    created_ad = ad_res.rowcount or 0

    _log(f"建档 {created_styles} 个款式、{created_goods} 个商品")
    _log(f"补建链接：千牛 {created_qn} 条，万相台 {created_ad} 条")
    _log("款式货号 | 推断类目 | 依据 | 支付金额（类目为推断值，请人工核对）：")
    for style_code, category, reason, pay in report:
        _log(f"  - {style_code} | {category} | {reason} | {pay}")
    if prefix_warnings:
        _log(f"以下 {len(prefix_warnings)} 个新货号与已有货号相似，请确认是否重复建档：")
        for style_code, similar in prefix_warnings:
            _log(f"  - 新建 {style_code}，已有 {similar}")

    qn_left = bind.execute(
        sa.text(
            """
            SELECT COUNT(*), COALESCE(SUM(q.pay_amount), 0)
            FROM qianniu_daily q
            WHERE q.platform_product_id IS NULL
              AND COALESCE(q.platform_id_snapshot, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM platform_product pp
                WHERE pp.tenant_id = q.tenant_id
                  AND pp.platform = '千牛'
                  AND pp.platform_id = q.platform_id_snapshot
              )
            """
        )
    ).one()
    orphan = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM style s WHERE s.is_deleted = false AND NOT EXISTS "
            "(SELECT 1 FROM goods_style_item g WHERE g.style_id = s.id)"
        )
    ).scalar_one()
    _log(f"仍未映射的千牛日报：{qn_left[0]} 条 / 支付 {qn_left[1]}（货号为 - 的需人工）")
    _log(f"未归属商品的在用款式：{orphan} 个（应为 0）")


def downgrade() -> None:
    # 按 remark 标记删除本迁移建出来的款式及其商品、链接。只删仍是"最小档案"
    # 的记录：一旦挂了 SKU 或被推广记录引用，说明已进入业务使用，保留不动。
    bind = op.get_bind()
    victims = bind.execute(
        sa.text(
            """
            SELECT s.id, s.style_code FROM style s
            WHERE s.remark LIKE '档案由千牛日报自动建立（migration 040）。%'
            """
        )
    ).all()
    keep: list[str] = []
    for style_id, style_code in victims:
        in_use = bind.execute(
            sa.text(
                """
                SELECT EXISTS (SELECT 1 FROM sku WHERE style_id = :s)
                    OR EXISTS (SELECT 1 FROM promotion WHERE style_id = :s)
                """
            ),
            {"s": style_id},
        ).scalar_one()
        if in_use:
            keep.append(style_code)
            continue
        bind.execute(
            sa.text("DELETE FROM platform_product WHERE style_id = :s"), {"s": style_id}
        )
        bind.execute(
            sa.text("DELETE FROM goods_style_item WHERE style_id = :s"), {"s": style_id}
        )
        bind.execute(
            sa.text(
                "DELETE FROM goods_main g WHERE g.goods_code = :c AND NOT EXISTS "
                "(SELECT 1 FROM goods_style_item i WHERE i.goods_main_id = g.id)"
            ),
            {"c": style_code},
        )
        bind.execute(sa.text("DELETE FROM style WHERE id = :s"), {"s": style_id})
    if keep:
        print(f"[040] 以下款式已进入业务使用，downgrade 保留：{', '.join(keep)}")
