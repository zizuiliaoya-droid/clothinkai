"""补建缺失的千牛/万相台链接映射，让已导入的日报数据进入投产报表。

生产现状（迁移前）：310 条千牛日报里有 106 条的 ``platform_id_snapshot`` 在
``platform_product`` 里找不到对应链接，合计支付金额 96,006.40 元进不了投产报表 ——
报表只看到全部销售额的 47%。万相台同样有 16 条、2,306.81 元广告花费悬空。

千牛日报的 ``extra`` 带「货号」字段，可以据此自动补映射。按货号分类后：

===========================  ======  ============  ========
原因                         链接数  支付金额        可自动
===========================  ======  ============  ========
款式未在系统建档               43     62,273.70     否
货号精确匹配款式货号            35     25,474.00     是
千牛后台未填货号（导出为 -）     14      7,350.00     否
货号填成了批次号                6        580.80      否
货号多了字母前缀                1        327.90      是
===========================  ======  ============  ========

本迁移只处理可自动的两类（36 个链接 / 25,801.90 元）。其余三类要么需要先补款式
档案，要么需要人工判断对应关系（「补差价专用」「双十一惊喜福袋」这类链接本身就
不对应单个款式，硬映射反而污染数据）。

万相台的 ``extra`` 没有货号，只有「主体ID」。万相台主体ID 就是淘宝商品ID，所以
靠同 ``platform_id`` 的千牛链接带出归属，其中 9 个链接、1,863.40 元可以补上。

不回填 ``qianniu_daily.platform_product_id``：报表 SQL 的 ``platform_id_snapshot``
兜底路径会自动匹配上新链接，留着不绑死，万一映射有误删掉链接就能还原。

Revision ID: 039_backfill_links
Revises: 038_merge_multilink
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039_backfill_links"
down_revision: str | Sequence[str] | None = "038_merge_multilink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 货号 -> 款式的匹配：先精确，再允许千牛侧多带的字母前缀（MY2026001 -> 2026001）。
#
# 不用 regexp_replace(货号, '^[A-Za-z]+', '') 剥前缀：那个量词是贪婪的，遇到
# 字母开头的款式货号（系统里有 NH022、KPFW... 这类）会把款号本身也吃掉，
# 例如 MYNH022 会被剥成 022 而不是 NH022。改成反向判断「款式货号是千牛货号的
# 后缀，且多出来的前缀全是字母」，并把前缀限制在 4 个字符内，避免过度宽松。
# 一个千牛ID 同时匹配多个款式时由下面的歧义守卫拦下，不会猜。
_MATCH = """
    (
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
"""

# 未映射：日报没有显式链接ID，且没有任何千牛链接占用这个 platform_id。
_UNMAPPED = """
    q.platform_product_id IS NULL
    AND COALESCE(q.platform_id_snapshot, '') <> ''
    AND NOT EXISTS (
      SELECT 1 FROM platform_product pp
      WHERE pp.tenant_id = q.tenant_id
        AND pp.platform = '千牛'
        AND pp.platform_id = q.platform_id_snapshot
    )
"""


def _log(msg: str) -> None:
    print(f"[039] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        f"""
        CREATE TEMP TABLE tmp_link_fix AS
        SELECT q.tenant_id,
               q.platform_id_snapshot AS platform_id,
               s.id AS style_id,
               s.style_code,
               MAX(q.extra->>'商品名称') AS title,
               COUNT(DISTINCT s.id) AS style_hits,
               SUM(q.pay_amount) AS pay_amount
        FROM qianniu_daily q
        JOIN style s
          ON s.tenant_id = q.tenant_id
         AND s.is_deleted = false
         AND {_MATCH}
        WHERE {_UNMAPPED}
        GROUP BY q.tenant_id, q.platform_id_snapshot, s.id, s.style_code
        """
    )

    # 一个千牛ID 匹配到多个款式说明货号有歧义，宁可不补也不猜。
    ambiguous = bind.execute(
        sa.text(
            """
            SELECT platform_id, string_agg(style_code, ', ' ORDER BY style_code)
            FROM tmp_link_fix
            GROUP BY platform_id
            HAVING COUNT(*) > 1
            ORDER BY platform_id
            """
        )
    ).all()
    if ambiguous:
        _log(f"以下 {len(ambiguous)} 个千牛ID 的货号匹配到多个款式，跳过，请人工确认：")
        for platform_id, codes in ambiguous:
            _log(f"  - {platform_id}: {codes}")
        op.execute(
            """
            DELETE FROM tmp_link_fix t
            WHERE EXISTS (
                SELECT 1 FROM tmp_link_fix o
                WHERE o.platform_id = t.platform_id AND o.style_id <> t.style_id
            )
            """
        )

    planned = bind.execute(
        sa.text(
            "SELECT platform_id, style_code, COALESCE(pay_amount, 0) FROM tmp_link_fix "
            "ORDER BY pay_amount DESC NULLS LAST"
        )
    ).all()
    if planned:
        _log(f"补建 {len(planned)} 条千牛链接（platform_id -> 款式货号 / 支付金额）：")
        for platform_id, style_code, pay in planned:
            _log(f"  - {platform_id} -> {style_code} / {pay}")
    else:
        _log("没有可自动补建的千牛链接")

    # goods_main 归属优先取非套装商品；款式只属于套装时（套装成员不会另建单件商品）
    # 就挂到套装上 —— 那是该款唯一的销售单元。
    op.execute(
        """
        INSERT INTO platform_product (
            id, tenant_id, platform, platform_id, style_id, title,
            goods_main_id, channel, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), t.tenant_id, '千牛', t.platform_id, t.style_id, t.title,
               (
                 SELECT i.goods_main_id
                 FROM goods_style_item i
                 JOIN goods_main g ON g.id = i.goods_main_id
                 WHERE i.style_id = t.style_id
                 ORDER BY g.is_suit, g.goods_code
                 LIMIT 1
               ),
               '普通', true, NOW(), NOW()
        FROM tmp_link_fix t
        WHERE NOT EXISTS (
            SELECT 1 FROM platform_product pp
            WHERE pp.tenant_id = t.tenant_id
              AND pp.platform = '千牛'
              AND pp.platform_id = t.platform_id
        )
        """
    )

    # 万相台：主体ID 即淘宝商品ID，沿用同 platform_id 的千牛链接归属。
    op.execute(
        """
        CREATE TEMP TABLE tmp_ad_fix AS
        SELECT DISTINCT a.tenant_id,
               a.platform_id_snapshot AS platform_id,
               src.style_id,
               src.goods_main_id,
               src.title
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
        """
    )
    ad_planned = bind.execute(
        sa.text(
            "SELECT t.platform_id, s.style_code FROM tmp_ad_fix t "
            "JOIN style s ON s.id = t.style_id ORDER BY t.platform_id"
        )
    ).all()
    if ad_planned:
        _log(f"补建 {len(ad_planned)} 条万相台链接（沿用同 ID 的千牛归属）：")
        for platform_id, style_code in ad_planned:
            _log(f"  - {platform_id} -> {style_code}")
    else:
        _log("没有可自动补建的万相台链接")

    op.execute(
        """
        INSERT INTO platform_product (
            id, tenant_id, platform, platform_id, style_id, title,
            goods_main_id, channel, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), t.tenant_id, '万相台', t.platform_id, t.style_id, t.title,
               t.goods_main_id, '普通', true, NOW(), NOW()
        FROM tmp_ad_fix t
        """
    )

    qn_unmapped = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*), COALESCE(SUM(q.pay_amount), 0)
            FROM qianniu_daily q WHERE {_UNMAPPED}
            """
        )
    ).one()
    ad_unmapped = bind.execute(
        sa.text(
            """
            SELECT COUNT(*), COALESCE(SUM(a.cost), 0)
            FROM ad_daily a
            WHERE a.platform_product_id IS NULL
              AND COALESCE(a.platform_id_snapshot, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM platform_product pp
                WHERE pp.tenant_id = a.tenant_id
                  AND pp.platform = '万相台'
                  AND pp.platform_id = a.platform_id_snapshot
              )
            """
        )
    ).one()
    links = bind.execute(
        sa.text("SELECT platform, COUNT(*) FROM platform_product GROUP BY platform ORDER BY 1")
    ).all()
    _log("链接总数：" + "，".join(f"{p}={n}" for p, n in links))
    _log(f"仍未映射的千牛日报：{qn_unmapped[0]} 条 / 支付 {qn_unmapped[1]}（款式未建档等，需人工）")
    _log(f"仍未映射的万相台日报：{ad_unmapped[0]} 条 / 花费 {ad_unmapped[1]}")

    op.execute("DROP TABLE IF EXISTS tmp_link_fix")
    op.execute("DROP TABLE IF EXISTS tmp_ad_fix")


def downgrade() -> None:
    # 不自动删除补出来的链接：它们可能已被人工编辑（改 channel、绑 SKU、改标题），
    # 按规则反推容易连带删掉原有记录。upgrade 日志里打印了完整的
    # platform_id -> 款式货号 清单，需要回滚时照清单删：
    #   DELETE FROM platform_product
    #   WHERE platform IN ('千牛','万相台') AND platform_id IN (...);
    # upgrade 本身带 NOT EXISTS 守卫，重复执行不会产生重复链接。
    pass
