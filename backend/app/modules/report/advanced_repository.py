"""U14 报表进阶聚合仓储（工作进度/爆款约篇/店铺/投产）。

只读聚合（text() 原生 SQL）+ 显式 WHERE tenant_id（RLS 之外防御层）。
比率指标由 service 层 safe_div 后处理（分母 0→null 语义统一）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import as_mapping, as_mappings
from app.modules.promotion.display_name import display_short_name_sql
from app.modules.promotion.urge_calculator import URGE_STATUS_SQL_EXPR, UrgeThresholds
from app.modules.report.pr_scope import PrParam, PrScope, pr_scope_from_param
from app.services.metric.publish_progress import like_sum_expr
from app.services.metric.work_progress import HIT_STAT_THRESHOLD

_URGE = URGE_STATUS_SQL_EXPR
_LIKE = like_sum_expr("p.like_count")


# ---------------------------------------------------------------------------
# 实时聚合与汇总表读取共用的 SQL 片段
#
# 汇总表读取路径（summary_read.py）必须与实时路径逐分相等。凡是两边都要写的表达式
# 都只在这里定义一次 —— 各写一份就是口径漂移的温床：改一边忘一边，汇总表和实时报表
# 对不上，而且不报错。
# ---------------------------------------------------------------------------

# 退款额与加购数读导入时解析好的 typed 列（055）。
#
# 以前这里是 ``extra->>'refund_amount'`` / ``extra->>'add_cart_count'`` 加正则守卫 ——
# 但导入写进 extra 的是生意参谋的**中文原始表头**，这两个英文键从来不存在，生产上所有
# 报表的退款与加购一直是 0，而且不报错。千分位、"-" 之类的脏值在 adapter 解析时处理，
# 报表这边只做加法。BI 店铺汇总与 BI 趋势也引用这里，不再各自内联一份。
_REFUND_SUM = "COALESCE(SUM(q.refund_amount), 0)"

_ADD_CART_SUM = "COALESCE(SUM(q.add_cart_count), 0)"

# 推广单的商品归属：推广记录自己记了商品（PR 录入时指定，可人工纠正）就用它；
# 为空时回落到「主商品」推定 —— 款式所在商品里非套装优先、货号次之。兜底只为兼容历史
# 数据与「款式还没归到商品」的异常，不是常规路径。
#
# 用法：``FROM promotion p {_PROMO_GOODS_LATERAL}``，归属 = ``COALESCE(p.goods_main_id,
# owner.goods_main_id)``。投产主表与报表下钻引用这一处（7e R1），口径不各写一份。
_PROMO_GOODS_LATERAL = """
LEFT JOIN LATERAL (
  SELECT gi.goods_main_id
  FROM goods_style_item gi
  JOIN goods_main gg ON gg.id = gi.goods_main_id
  WHERE gi.style_id = p.style_id AND gi.is_active = true
  ORDER BY gg.is_suit, gg.goods_code
  LIMIT 1
) owner ON p.goods_main_id IS NULL"""

_PROMO_GOODS_ID = "COALESCE(p.goods_main_id, owner.goods_main_id)"

# 商品维度报表的「商品信息」列（投产主表与它的汇总表读取共用）
GOODS_META_COLUMNS = """
  g.id AS goods_id, g.goods_code AS goods_code,
  g.goods_title AS goods_title, g.short_name AS goods_short_name, g.is_suit AS is_suit,
  -- 商品主图只取成员款式已上传的主图（8a-2，设计 §7.2：goods_main.main_image_key 是 037 / 041
  -- 从款式复制来的 key，款式换图后成了死链，已废弃不读）
  (
    SELECT ms.main_image_key
    FROM goods_style_item mi
    JOIN style ms ON ms.id = mi.style_id
    WHERE mi.goods_main_id = g.id AND mi.is_active = true
      AND ms.main_image_key IS NOT NULL
    ORDER BY mi.sort_order, ms.style_code
    LIMIT 1
  ) AS main_image_key,
  -- 套装要让人看出含哪几款；单品就是它自己的货号
  (
    SELECT string_agg(cs.style_code, ',' ORDER BY ci.sort_order, cs.style_code)
    FROM goods_style_item ci
    JOIN style cs ON cs.id = ci.style_id
    WHERE ci.goods_main_id = g.id AND ci.is_active = true
  ) AS style_codes"""

# 投产主表按（商品, 渠道）出行（7e R2）的可加列。外层 SELECT（实时与汇总读取共用）
# 与 ProductionService.get_goods_rows 按商品求和都引这两个元组，不各写一份列名。
PRODUCTION_MONEY_COLUMNS: tuple[str, ...] = (
    "pay_amount",
    "refund_amount",
    "promo_cost",
    "ad_spend",
)
PRODUCTION_COUNT_COLUMNS: tuple[str, ...] = (
    "add_cart_count",
    "pay_orders",
    "ad_add_cart_count",
    "paired_all_cart",
    "paired_ad_cart",
    "paired_days",
)
PRODUCTION_ADDITIVE_COLUMNS: tuple[str, ...] = (
    *PRODUCTION_MONEY_COLUMNS,
    *PRODUCTION_COUNT_COLUMNS,
)


def goods_channel_select(filter_sql: str) -> str:
    """投产主表的外层：接在名为 ``per_channel`` 的 CTE 后面（``WITH …, per_channel AS (…),``）。

    ``per_channel`` 每（商品, 渠道）一行，列 = ``goods_id``、``channel``、``pay_raw``（含刷单的
    销售原值）、``pay_amount``（按开关扣过刷单）与 ``PRODUCTION_ADDITIVE_COLUMNS`` 其余各列。

    过滤与排序看**商品级合计**（窗口按商品求和），不看单个渠道行：行集合 = 改动前按商品
    聚合的 HAVING（含刷单的销售原值、站外、站内三项任一 > 0），排序 = 商品级支付额降序、
    货号、商品 id、渠道（普通在前）。逐行 HAVING 会丢掉「直播整段只有退款」这种行，按商品
    求和（``ProductionService.get_goods_rows``）后就和旧数对不上了。同一商品的渠道行相邻。
    """
    money = ",\n".join(f"r.{c} AS {c}" for c in PRODUCTION_MONEY_COLUMNS)
    counts = ",\n".join(f"CAST(r.{c} AS bigint) AS {c}" for c in PRODUCTION_COUNT_COLUMNS)
    return f"""
    goods_rows AS (
      SELECT pc.*,
             SUM(pc.pay_raw) OVER w AS goods_pay_raw,
             SUM(pc.promo_cost) OVER w AS goods_promo_cost,
             SUM(pc.ad_spend) OVER w AS goods_ad_spend,
             SUM(pc.pay_amount) OVER w AS goods_pay_amount
      FROM per_channel pc
      JOIN goods_main g ON g.id = pc.goods_id
      WHERE g.tenant_id = :tenant_id AND g.is_deleted = false
        {filter_sql}
      WINDOW w AS (PARTITION BY pc.goods_id)
    )
    SELECT
      {GOODS_META_COLUMNS},
      r.channel AS channel,
      {money},
      {counts}
    FROM goods_rows r
    JOIN goods_main g ON g.id = r.goods_id
    WHERE r.goods_pay_raw > 0 OR r.goods_promo_cost > 0 OR r.goods_ad_spend > 0
    -- 支付额相同的商品原来顺序不确定；货号兜底后汇总表读取能逐行比对。
    -- 再按商品 id、渠道（普通在前，不靠中文排序规则）兜底：同一商品的两行相邻
    ORDER BY r.goods_pay_amount DESC, g.goods_code, g.id,
             CASE WHEN r.channel = '普通' THEN 0 ELSE 1 END
    """


# 日期分桶。周按 ISO（周一开始），汇总表刷新的 _bucket_start 用同一口径 ——
# 两边分桶方式不一致会让周汇总与周趋势图对不上。
_BUCKET_TEMPLATES = {
    "day": "{column}",
    "week": "date_trunc('week', {column})::date",
    "month": "date_trunc('month', {column})::date",
    "year": "date_trunc('year', {column})::date",
}


def bucket_expr(granularity: str, column: str) -> str:
    """``column`` 按 ``granularity`` 分桶后的 SQL 表达式（桶的首日）。"""
    try:
        template = _BUCKET_TEMPLATES[granularity]
    except KeyError as exc:
        raise ValueError(f"Unsupported granularity: {granularity}") from exc
    return template.format(column=column)


def goods_filter_clauses(seasons: Sequence[str] | None) -> tuple[str, dict[str, Any]]:
    """商品维度的季节多选筛选（PRD 第 4 章）。空列表与 None 同义：不筛。

    类目筛选已下线（8a-3，J12）：实时与汇总两条读路径都只剩季节。
    ``= ANY(:param)`` 走数组绑定参数，不把值拼进 SQL。
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if seasons:
        clauses.append("AND g.season = ANY(:seasons)")
        params["seasons"] = list(seasons)
    return "\n".join(clauses), params


# 千牛日报 → 商品 的映射，物化成 CTE。
#
# 这段逻辑原来直接写在 ``LEFT JOIN qianniu_daily ON (...)`` 的条件里，于是
# PostgreSQL 对**每个 (商品, 日报) 组合**都要跑一遍里面的 EXISTS / NOT EXISTS /
# COUNT(DISTINCT)。生产实测：264 个商品 × 310 行日报 = 81,840 次，
# `Rows Removed by Join Filter: 81550`、`Buffers: shared hit=237381`，
# 整条投产 SQL 要 1848ms —— 处理 310 行数据。
#
# 提成 CTE 之后映射只算一次（日报行数量级），主查询退化成等值 JOIN。
#
# 口径与原来逐字对应，两条分支：
# 1. 正路：platform_product 里建了千牛链接并绑定了商品
# 2. 兜底：链接没建，但款式上手填了千牛ID。护栏是「这个千牛ID 只能落在一个商品」
#    —— 多个款式共用一条链接正是套装的形态，所以按商品数而不是款式数去重。
#    生产上这条分支只覆盖 20 行日报（``style.qianniu_product_id`` 非空仅 2 条），
#    却因为写在 JOIN 条件里拖掉了整条查询 67% 的时间。
#
# 停用链接（7e-9）：正路只认 ``is_active`` 的链接，停用链接的日报不进单品，
# 店铺 / BI 店铺总览不走这里、照旧全店。兜底的 ``NOT EXISTS`` 刻意**不**看
# ``is_active``：停用的链接照样挡住兜底，否则停用后反而被兜底算回单品。
#
# 渠道（7e R2）：正路带出链接的 ``channel``，兜底没有链接、记「普通」。两条分支按
# 日报互斥（兜底要求没有同 ID 的千牛链接），所以加了这一列 UNION 也不会多出行。
_QIANNIU_GOODS_LINK_CTE = """
qn_link AS (
    SELECT q.id AS qid, pp.goods_main_id AS goods_id, pp.channel AS channel
    FROM qianniu_daily q
    JOIN platform_product pp
      ON pp.tenant_id = q.tenant_id
     AND pp.platform = '千牛'
     AND pp.goods_main_id IS NOT NULL
     AND pp.is_active = true
     AND (
       (q.platform_product_id IS NOT NULL AND pp.id = q.platform_product_id)
       OR (q.platform_product_id IS NULL AND pp.platform_id = q.platform_id_snapshot)
     )
    WHERE q.tenant_id = :tenant_id AND q.date BETWEEN :date_from AND :date_to

    UNION

    SELECT q.id AS qid, legacy.goods_main_id, CAST('普通' AS varchar(16)) AS channel
    FROM qianniu_daily q
    JOIN (
      -- 按千牛ID 预聚合：它落在几个商品上、落在哪个。只有唯一归属才认
      SELECT s.tenant_id,
             s.qianniu_product_id AS pid,
             (array_agg(DISTINCT gi.goods_main_id))[1] AS goods_main_id,
             COUNT(DISTINCT gi.goods_main_id) AS goods_cnt
      FROM style s
      JOIN goods_style_item gi ON gi.style_id = s.id AND gi.is_active = true
      WHERE s.is_deleted = false AND s.qianniu_product_id IS NOT NULL
      GROUP BY s.tenant_id, s.qianniu_product_id
    ) legacy
      ON legacy.tenant_id = q.tenant_id
     AND legacy.pid = q.platform_id_snapshot
     AND legacy.goods_cnt = 1
    WHERE q.tenant_id = :tenant_id
      AND q.date BETWEEN :date_from AND :date_to
      AND q.platform_product_id IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM platform_product pp2
        WHERE pp2.tenant_id = q.tenant_id
          AND pp2.platform = '千牛'
          AND pp2.platform_id = q.platform_id_snapshot
      )
)
"""

# 逐日配对加购（7e R2，设计 3.2），接在 ``qn_link`` 后面用：``WITH {qn_link}, {本段}``。
#
# 同一（商品, 渠道, 日）千牛与万相台**都有行**才配对：``paired_days = 1``，两列各取当天
# 的加购和（NULL 记 0，行在就算有）；只有一边的日子三列都是 0（不出行）。必须逐日判断：
# 整段区间判一次「两边都有数据」，千牛 D1、D2 + 万相台只有 D2 时会把 D1 的千牛加购也算进去。
# 按天存原值，取 0 / 求差留给读取侧在求和之后做（两天 +5 / −8 合计 −3，不是逐日取 0 的 5）。
#
# 千牛一侧走 ``qn_link``（停用链接与兜底口径同主表）；万相台一侧与 ``ads`` 同口径：
# 映射到商品的启用链接，渠道取万相台链接的 ``channel``。
# ``daily_trend_by_goods`` 与 ``aggregate_by_goods`` 共用这一份，不各写一遍。
_PAIRED_CART_CTE = """
paired_qn AS (
    SELECT qn_link.goods_id, qn_link.channel, q.date AS d,
           COALESCE(SUM(q.add_cart_count), 0) AS cart
    FROM qn_link
    JOIN qianniu_daily q ON q.id = qn_link.qid
    GROUP BY qn_link.goods_id, qn_link.channel, q.date
),
paired_ad AS (
    SELECT mapped_a.goods_main_id AS goods_id, mapped_a.channel, a.date AS d,
           COALESCE(SUM(a.add_cart_count), 0) AS cart
    FROM ad_daily a
    JOIN platform_product mapped_a
      ON mapped_a.tenant_id = a.tenant_id
     AND mapped_a.platform = '万相台'
     AND mapped_a.goods_main_id IS NOT NULL
     AND mapped_a.is_active = true
     AND (
       (a.platform_product_id IS NOT NULL AND mapped_a.id = a.platform_product_id)
       OR (a.platform_product_id IS NULL AND mapped_a.platform_id = a.platform_id_snapshot)
     )
    WHERE a.tenant_id = :tenant_id AND a.date BETWEEN :date_from AND :date_to
    GROUP BY mapped_a.goods_main_id, mapped_a.channel, a.date
),
paired_cart AS (
    SELECT pq.goods_id, pq.channel, pq.d,
           pq.cart AS paired_all_cart,
           pa.cart AS paired_ad_cart,
           1 AS paired_days
    FROM paired_qn pq
    JOIN paired_ad pa
      ON pa.goods_id = pq.goods_id AND pa.channel = pq.channel AND pa.d = pq.d
)
"""


class WorkProgressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_by_pr(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        today: date,
        thresholds: UrgeThresholds,
        by_goods: bool,
        seasons: Sequence[str] | None = None,
        pr_id: PrParam = None,
    ) -> list[Mapping[str, Any]]:
        """按 PR（``by_goods`` 时按 PR × 归属商品）聚合区间内的推广单。

        - ``thresholds`` 决定档期内 / 催发 / 重要催发三档的分界，必须是租户配置
        - ``by_goods`` 必填、不给默认值：汇总表刷新要 True（行多一列 ``goods_main_id``，
          归属 = ``_PROMO_GOODS_LATERAL``，没归属商品的单落在 NULL 那一行），报表读取要 False
        - ``seasons``：只统计归属商品季节命中的单（没归属商品的单不算）
        - ``pr_id``：None 不过滤、UUID 只看该 PR、``UNASSIGNED`` 只看 ``pr_id IS NULL``
          （7e R2 把 R1 在 service 层的「只看本人」下推到这里）
        """
        scope_sql, scope_params = pr_scope_from_param(pr_id).sql("p.pr_id")
        season_sql, season_params = goods_filter_clauses(seasons)
        # 归属 LATERAL 只在要商品维度或季节时带上，按 PR 汇总的老形态不多做一次 JOIN
        owner_join = ""
        if by_goods or seasons:
            owner_join = _PROMO_GOODS_LATERAL
        if seasons:
            owner_join += f"\n            LEFT JOIN goods_main g ON g.id = {_PROMO_GOODS_ID}"
        goods_select = f"{_PROMO_GOODS_ID} AS goods_main_id," if by_goods else ""
        goods_group = f", {_PROMO_GOODS_ID}" if by_goods else ""
        goods_order = ", goods_main_id NULLS LAST" if by_goods else ""
        sql = text(
            f"""
            SELECT
              p.pr_id AS pr_id,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              {goods_select}
              COUNT(*) AS quote_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '档期内') AS in_schedule_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '催发') AS urge_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '重要催发') AS important_urge_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '超时') AS overdue_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布') AS publish_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布' AND p.like_count IS NOT NULL)
                AS info_complete_count,
              COUNT(*) FILTER (WHERE p.publish_status='已取消') AS cancel_count,
              COUNT(*) FILTER (WHERE p.recall_status IN ('召回中','召回成功','召回失败'))
                AS recall_due_count,
              COUNT(*) FILTER (WHERE p.recall_status='召回成功') AS recall_success_count,
              -- 完成率/超时率的分母：约稿量扣掉召回与取消。
              -- 用 FILTER 一次算出，而不是 quote_count - recall_due - cancel ——
              -- 同一单据可能既已取消又进过召回，相减会把它扣两次。
              COUNT(*) FILTER (
                WHERE p.publish_status <> '已取消'
                  AND p.recall_status = '未召回'
              ) AS effective_quote_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布'
                               AND p.like_count >= :hit_stat) AS hit_count,
              {_LIKE} AS like_count,
              COALESCE(SUM(p.cost_snapshot), 0) AS cost,
              -- 7e R2：服务费四分 + 推广成本 + 未发布 / 发货计数（R5 的公式用）
              COALESCE(SUM(p.quote_amount), 0) AS quote_amount,
              COALESCE(SUM(p.quote_amount) FILTER (WHERE p.publish_status='已发布'), 0)
                AS published_quote_amount,
              -- 有效约稿服务费：与 effective_quote_count 同一个 FILTER（扣取消与召回，不重复扣）
              COALESCE(SUM(p.quote_amount) FILTER (
                WHERE p.publish_status <> '已取消'
                  AND p.recall_status = '未召回'
              ), 0) AS effective_quote_amount,
              COALESCE(SUM(p.quote_amount) FILTER (WHERE p.publish_status='未发布'), 0)
                AS unpublished_quote_amount,
              COALESCE(SUM(p.quote_amount) FILTER (WHERE p.publish_status='已取消'), 0)
                AS cancelled_quote_amount,
              -- 推广成本 = 未删除推广单的应付（服务费 + 样品 + 运费）之和，不看发布 / 召回状态：
              -- 未发布就召回的运费、样品也算（设计默 8 / 默 17）
              COALESCE(SUM(p.total_promo_cost), 0) AS promo_cost,
              COUNT(*) FILTER (WHERE p.publish_status='未发布') AS unpublished_count,
              -- 「已发货」= 流程线 PR-2 的 ship_status（060）
              COUNT(*) FILTER (WHERE p.ship_status='已发货') AS shipped_count,
              COUNT(*) FILTER (WHERE p.ship_status='已发货' AND p.publish_status='已发布')
                AS shipped_published_count
            FROM promotion p
            {owner_join}
            LEFT JOIN "user" u ON u.id = p.pr_id
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.cooperation_date BETWEEN :date_from AND :date_to
              {scope_sql}
              {season_sql}
            GROUP BY p.pr_id, u.display_name, u.username{goods_group}
            -- 约稿量相同的 PR 原来顺序不确定；加上姓名与 id 兜底，
            -- 汇总表读取（summary_read）用同一排序，两条路径才能逐行比对
            ORDER BY quote_count DESC, pr_name, pr_id{goods_order}
            """
        )
        params = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            "today": today,
            **thresholds.sql_params(),
            "hit_stat": HIT_STAT_THRESHOLD,
            **scope_params,
            **season_params,
        }
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    async def goods_display_meta(
        self, tenant_id: UUID, ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, str | None]]:
        """商品 id → (全称, 简称)，单品卡片两条路径共用（7e R5）。

        不过滤 ``is_deleted``：推广单归属到已删除商品时卡片照样要有这一行，
        否则「商品行 + 未归属 = PR 表合计」对不上。不取商品编码。
        """
        if not ids:
            return {}
        sql = text(
            """
            SELECT g.id AS id, g.goods_title AS goods_title, g.short_name AS short_name
            FROM goods_main g
            WHERE g.tenant_id = :tenant_id AND g.id = ANY(CAST(:ids AS uuid[]))
            """
        )
        rows = (await self._s.execute(sql, {"tenant_id": tenant_id, "ids": list(ids)})).all()
        return {r.id: (r.goods_title, r.short_name) for r in rows}


class TargetPlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_with_actuals(self, *, tenant_id: UUID, month: str) -> list[Mapping[str, Any]]:
        sql = text(
            """
            SELECT
              t.id AS id, t.pr_id AS pr_id, t.style_id AS style_id,
              t.period_month AS period_month, t.min_target AS min_target,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              s.style_code AS style_code, s.style_name AS style_name,
              COALESCE(act.actual, 0) AS actual_count
            FROM target_planning t
            JOIN style s ON s.id = t.style_id
            LEFT JOIN "user" u ON u.id = t.pr_id
            LEFT JOIN (
              SELECT pr_id, style_id, COUNT(*) AS actual FROM promotion
              WHERE tenant_id = :tenant_id AND is_active = true
                AND to_char(cooperation_date, 'YYYY-MM') = :month
              GROUP BY pr_id, style_id
            ) act ON act.pr_id = t.pr_id AND act.style_id = t.style_id
            WHERE t.tenant_id = :tenant_id AND t.period_month = :month
            ORDER BY s.style_code
            """
        )
        return as_mappings(
            (await self._s.execute(sql, {"tenant_id": tenant_id, "month": month})).mappings().all()
        )


class StoreDailyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """店铺按日聚合（全店口径），只按天；``shop_daily_summary`` 的刷新源。

        日期轴（7e R2，设计 2.4「日期并集」）= 千牛日报 ∪ 万相台 ∪ 已发布推广单合作日期
        ∪ 刷单（``exclude_from_roi``）∪ ``store_daily`` 任一有行的日子，不再只列有千牛日报
        的日子。全店：停用链接、未映射到商品的日报都算（问题 D）。「其中直播」两列按
        链接 ``channel = '直播'``，不看 ``is_active`` / ``goods_main_id``。

        手填层（``store_daily`` 的 4 列，含仅退款）LEFT JOIN、不 COALESCE：没录就是 NULL，
        与汇总读取（``SummaryReadRepository.store_daily``）同一套。
        """
        sql = text(
            f"""
            WITH days AS (
              SELECT q.date AS d FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id AND q.date BETWEEN :date_from AND :date_to
              UNION
              SELECT a.date FROM ad_daily a
              WHERE a.tenant_id = :tenant_id AND a.date BETWEEN :date_from AND :date_to
              UNION
              SELECT p.cooperation_date FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              UNION
              SELECT oa.order_date FROM order_adjustment oa
              WHERE oa.tenant_id = :tenant_id AND oa.exclude_from_roi = true
                AND oa.order_date BETWEEN :date_from AND :date_to
              UNION
              SELECT sd.date FROM store_daily sd
              WHERE sd.tenant_id = :tenant_id AND sd.date BETWEEN :date_from AND :date_to
            ),
            qn AS (
              SELECT q.date AS d,
                     COALESCE(SUM(q.visitors), 0) AS visitors,
                     COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
                     COALESCE(SUM(q.pay_orders), 0) AS pay_orders,
                     {_REFUND_SUM} AS refund_amount,
                     COALESCE(SUM(q.pay_amount) FILTER (WHERE EXISTS (
                       SELECT 1 FROM platform_product lp
                       WHERE lp.tenant_id = q.tenant_id
                         AND lp.platform = '千牛'
                         AND lp.channel = '直播'
                         AND (
                           (q.platform_product_id IS NOT NULL AND lp.id = q.platform_product_id)
                           OR (q.platform_product_id IS NULL
                               AND lp.platform_id = q.platform_id_snapshot)
                         )
                     )), 0) AS live_pay_amount
              FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id AND q.date BETWEEN :date_from AND :date_to
              GROUP BY q.date
            ),
            ads AS (
              SELECT a.date AS d,
                     COALESCE(SUM(a.cost), 0) AS ad_spend,
                     COALESCE(SUM(a.cost) FILTER (WHERE EXISTS (
                       SELECT 1 FROM platform_product lp
                       WHERE lp.tenant_id = a.tenant_id
                         AND lp.platform = '万相台'
                         AND lp.channel = '直播'
                         AND (
                           (a.platform_product_id IS NOT NULL AND lp.id = a.platform_product_id)
                           OR (a.platform_product_id IS NULL
                               AND lp.platform_id = a.platform_id_snapshot)
                         )
                     )), 0) AS live_ad_spend
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id AND a.date BETWEEN :date_from AND :date_to
              GROUP BY a.date
            ),
            promos AS (
              -- 站外推广成本 = 当天合作日期的已发布推广单（全店，含没归属商品的单）
              SELECT p.cooperation_date AS d,
                     COALESCE(SUM(p.total_promo_cost), 0) AS promo_cost
              FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY p.cooperation_date
            ),
            brushing AS (
              -- 全部 exclude_from_roi 调整，不要求款式能归到商品
              SELECT oa.order_date AS d, COALESCE(SUM(oa.amount), 0) AS brushing_amount
              FROM order_adjustment oa
              WHERE oa.tenant_id = :tenant_id AND oa.exclude_from_roi = true
                AND oa.order_date BETWEEN :date_from AND :date_to
              GROUP BY oa.order_date
            )
            SELECT
              days.d AS date,
              COALESCE(qn.visitors, 0) AS visitors,
              COALESCE(qn.pay_amount, 0) AS pay_amount,
              COALESCE(qn.pay_orders, 0) AS pay_orders,
              COALESCE(qn.refund_amount, 0) AS refund_amount,
              COALESCE(brushing.brushing_amount, 0) AS brushing_amount,
              COALESCE(ads.ad_spend, 0) AS ad_spend,
              COALESCE(promos.promo_cost, 0) AS promo_cost,
              COALESCE(qn.live_pay_amount, 0) AS live_pay_amount,
              COALESCE(ads.live_ad_spend, 0) AS live_ad_spend,
              sd.ad_spend_total AS ad_spend_total,
              sd.zhitongche_spend AS zhitongche_spend,
              sd.yinli_spend AS yinli_spend,
              sd.refund_only_amount AS refund_only_amount
            FROM days
            LEFT JOIN qn ON qn.d = days.d
            LEFT JOIN ads ON ads.d = days.d
            LEFT JOIN promos ON promos.d = days.d
            LEFT JOIN brushing ON brushing.d = days.d
            LEFT JOIN store_daily sd
              ON sd.tenant_id = :tenant_id AND sd.date = days.d
            ORDER BY days.d
            """
        )
        return as_mappings(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .all()
        )


class ProductionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_by_goods(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        exclude_brushing: bool = False,
        seasons: Sequence[str] | None = None,
    ) -> list[Mapping[str, Any]]:
        """按（商品, 渠道）聚合；``qn_link`` CTE 保证每条日报只映射一次。

        对齐 PRD V1.4 第 3 章：报表主体是「商品」而不是「款式」。套装（多个款式共用
        一条销售链接）合并成一行，销售额只算一次；套装的站外推广费与刷单剔除按成员
        款式求和。

        渠道（7e R2，设计 3.2）：与 ``daily_trend_by_goods`` 同口径 —— 销售 / 退款 / 加购 /
        支付单数按千牛链接的 ``channel``（兜底记普通），站内花费与站内加购按万相台链接的
        ``channel``，推广与刷单记普通；配对加购走同一份 ``_PAIRED_CART_CTE``（逐日配对后
        再按（商品, 渠道）求和）。过滤与排序看商品级合计（``goods_channel_select``）。
        投产接口按（商品, 渠道）出行（7e R3）；BI / 企微 / AI 走 ``ProductionService.get_goods_rows``
        按商品求和回一行。

        ``seasons`` 为多选（PRD 第 4 章），空列表与 None 同义：不筛（类目筛选已下线，8a-3）。
        """
        filter_sql, filter_params = goods_filter_clauses(seasons)
        # 款式理论上可以同时属于单品商品与套装（库层面表达不了这个约束），那时同一笔
        # 调整会被两个商品各减一次。取「主商品」（非套装优先、货号次之）保证只归一处，
        # 宁可归属有偏差也不让总额虚高。彻底的解法是给 order_adjustment 直接记
        # goods_main_id，留给后续批次。
        sql = text(
            f"""
            WITH {_QIANNIU_GOODS_LINK_CTE}, {_PAIRED_CART_CTE},
            sales AS (
              -- 映射走 qn_link CTE（见 _QIANNIU_GOODS_LINK_CTE），这里只剩等值 JOIN。
              -- 以前这一段是一大段 EXISTS/NOT EXISTS/COUNT(DISTINCT) 写在 JOIN 条件里，
              -- 对每个 (商品, 日报) 组合各算一遍，310 行数据要 1848ms。
              SELECT qn_link.goods_id AS goods_id, qn_link.channel AS channel,
                     COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
                     {_REFUND_SUM} AS refund_amount,
                     {_ADD_CART_SUM} AS add_cart_count,
                     COALESCE(SUM(q.pay_orders), 0) AS pay_orders
              FROM qn_link
              JOIN qianniu_daily q ON q.id = qn_link.qid
              GROUP BY qn_link.goods_id, qn_link.channel
            ),
            ads AS (
              SELECT mapped_a.goods_main_id AS goods_id, mapped_a.channel AS channel,
                     COALESCE(SUM(a.cost), 0) AS ad_spend,
                     COALESCE(SUM(a.add_cart_count), 0) AS ad_add_cart_count
              FROM ad_daily a
              JOIN platform_product mapped_a
                ON mapped_a.tenant_id = a.tenant_id
                AND mapped_a.platform = '万相台'
                AND mapped_a.goods_main_id IS NOT NULL
                -- 停用链接的站内花费不进单品（7e-9）
                AND mapped_a.is_active = true
                AND (
                  (a.platform_product_id IS NOT NULL
                   AND mapped_a.id = a.platform_product_id)
                  OR (a.platform_product_id IS NULL
                      AND mapped_a.platform_id = a.platform_id_snapshot)
                )
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
              GROUP BY mapped_a.goods_main_id, mapped_a.channel
            ),
            promos AS (
              -- 商品归属：自己记的归属优先，为空回落主商品推定（_PROMO_GOODS_LATERAL）
              SELECT {_PROMO_GOODS_ID} AS goods_id,
                     -- 站外推广成本 = 博主服务费 + 样品成本 + 寄回运费（PRD V1.4 §9）。
                     -- total_promo_cost 是生成列，口径集中在数据库，报表只读不算。
                     COALESCE(SUM(p.total_promo_cost), 0) AS promo_cost
              FROM promotion p
              {_PROMO_GOODS_LATERAL}
              WHERE p.tenant_id = :tenant_id
                AND p.cooperation_date BETWEEN :date_from AND :date_to
                AND p.is_active = true AND p.publish_status = '已发布'
              GROUP BY {_PROMO_GOODS_ID}
            ),
            brushing AS (
              -- 剔除与否只看 exclude_from_roi，不再叠加 order_type 判断：
              -- 标记本身就是「要不要进投产口径」的唯一开关，两个条件并列反而容易漏。
              SELECT brush_goods.goods_main_id AS goods_id,
                     COALESCE(SUM(oa.amount), 0) AS brushing_amount
              FROM order_adjustment oa
              CROSS JOIN LATERAL (
                SELECT gi.goods_main_id FROM goods_style_item gi
                JOIN goods_main gg ON gg.id = gi.goods_main_id
                WHERE gi.style_id = oa.style_id AND gi.is_active = true
                ORDER BY gg.is_suit, gg.goods_code
                LIMIT 1
              ) brush_goods
              WHERE oa.tenant_id = :tenant_id
                AND oa.exclude_from_roi = true
                AND oa.order_date BETWEEN :date_from AND :date_to
              GROUP BY brush_goods.goods_main_id
            ),
            paired AS (
              -- 配对已在 _PAIRED_CART_CTE 里逐日判断，这里只按（商品, 渠道）求和
              SELECT pc.goods_id AS goods_id, pc.channel AS channel,
                     SUM(pc.paired_all_cart) AS paired_all_cart,
                     SUM(pc.paired_ad_cart) AS paired_ad_cart,
                     SUM(pc.paired_days) AS paired_days
              FROM paired_cart pc
              GROUP BY pc.goods_id, pc.channel
            ),
            per_channel AS (
              SELECT goods_id, channel,
                     COALESCE(SUM(pay_amount), 0) AS pay_raw,
                     COALESCE(SUM(pay_amount), 0)
                       - CASE WHEN :exclude_brushing
                              THEN COALESCE(SUM(brushing_amount), 0)
                              ELSE 0 END AS pay_amount,
                     COALESCE(SUM(refund_amount), 0) AS refund_amount,
                     COALESCE(SUM(add_cart_count), 0) AS add_cart_count,
                     COALESCE(SUM(pay_orders), 0) AS pay_orders,
                     COALESCE(SUM(promo_cost), 0) AS promo_cost,
                     COALESCE(SUM(ad_spend), 0) AS ad_spend,
                     COALESCE(SUM(ad_add_cart_count), 0) AS ad_add_cart_count,
                     COALESCE(SUM(paired_all_cart), 0) AS paired_all_cart,
                     COALESCE(SUM(paired_ad_cart), 0) AS paired_ad_cart,
                     COALESCE(SUM(paired_days), 0) AS paired_days
              FROM (
                SELECT goods_id, channel, pay_amount, refund_amount, add_cart_count,
                       pay_orders,
                       0::numeric AS brushing_amount,
                       0::numeric AS promo_cost, 0::numeric AS ad_spend,
                       0::bigint AS ad_add_cart_count,
                       0::bigint AS paired_all_cart, 0::bigint AS paired_ad_cart,
                       0::bigint AS paired_days
                FROM sales
                UNION ALL
                SELECT goods_id, CAST('普通' AS varchar(16)), 0, 0, 0, 0, brushing_amount,
                       0, 0, 0, 0, 0, 0
                FROM brushing
                UNION ALL
                SELECT goods_id, channel, 0, 0, 0, 0, 0, 0, ad_spend, ad_add_cart_count,
                       0, 0, 0
                FROM ads
                UNION ALL
                SELECT goods_id, CAST('普通' AS varchar(16)), 0, 0, 0, 0, 0, promo_cost,
                       0, 0, 0, 0, 0
                FROM promos
                UNION ALL
                SELECT goods_id, channel, 0, 0, 0, 0, 0, 0, 0, 0,
                       paired_all_cart, paired_ad_cart, paired_days
                FROM paired
              ) source
              GROUP BY goods_id, channel
            ),
            {goods_channel_select(filter_sql)}
            """
        )
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            "exclude_brushing": exclude_brushing,
            **filter_params,
        }
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    async def link_counts(
        self, *, tenant_id: UUID, goods_ids: Sequence[UUID]
    ) -> dict[tuple[UUID, str], int]:
        """（商品, 渠道）→ 启用的千牛链接数（7e R3，页面「N 条链接」）。

        看当前状态、不看区间：链接今天停用了就不算。汇总 / 实时两路共用这一条。
        """
        if not goods_ids:
            return {}
        sql = text(
            """
            SELECT pp.goods_main_id AS goods_id, pp.channel AS channel, COUNT(*) AS n
            FROM platform_product pp
            WHERE pp.tenant_id = :tenant_id
              AND pp.platform = '千牛'
              AND pp.is_active = true
              AND pp.goods_main_id = ANY(CAST(:goods_ids AS uuid[]))
            GROUP BY pp.goods_main_id, pp.channel
            """
        )
        rows = (
            await self._s.execute(sql, {"tenant_id": tenant_id, "goods_ids": list(goods_ids)})
        ).mappings()
        return {(r["goods_id"], r["channel"]): int(r["n"]) for r in rows}

    async def inactive_link_count(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        seasons: Sequence[str] | None,
    ) -> int:
        """区间内有数据、但因停用没计入单品的链接数（7e R3，设计 2.9 的展示部分）。

        口径：``is_active = false``、有归属商品（商品未删、过季节筛选）的千牛 / 万相台链接，
        区间内有千牛日报**行**或万相台花费**行**（金额 0 的行也算：数据进来了、被排除了）。
        日报对链接同 ``qn_link`` / ``ads``：有 FK 按 FK，没回填按 ``platform_id`` 快照，
        平台各配各的。汇总 / 实时两路都调这一条（汇总表不存停用链接的数），不随刷单开关。
        """
        filter_sql, filter_params = goods_filter_clauses(seasons)
        sql = text(
            f"""
            SELECT COUNT(DISTINCT pp.id) AS n
            FROM platform_product pp
            JOIN goods_main g ON g.id = pp.goods_main_id
            WHERE pp.tenant_id = :tenant_id
              AND pp.is_active = false
              AND g.tenant_id = :tenant_id AND g.is_deleted = false
              {filter_sql}
              AND (
                (pp.platform = '千牛' AND EXISTS (
                  SELECT 1 FROM qianniu_daily q
                  WHERE q.tenant_id = pp.tenant_id
                    AND q.date BETWEEN :date_from AND :date_to
                    AND (
                      (q.platform_product_id IS NOT NULL AND q.platform_product_id = pp.id)
                      OR (q.platform_product_id IS NULL
                          AND q.platform_id_snapshot = pp.platform_id)
                    )
                ))
                OR (pp.platform = '万相台' AND EXISTS (
                  SELECT 1 FROM ad_daily a
                  WHERE a.tenant_id = pp.tenant_id
                    AND a.date BETWEEN :date_from AND :date_to
                    AND (
                      (a.platform_product_id IS NOT NULL AND a.platform_product_id = pp.id)
                      OR (a.platform_product_id IS NULL
                          AND a.platform_id_snapshot = pp.platform_id)
                    )
                ))
              )
            """
        )
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            **filter_params,
        }
        return int((await self._s.execute(sql, params)).scalar_one())

    async def goods_unit_costs(
        self, *, tenant_id: UUID, goods_ids: Sequence[UUID]
    ) -> dict[UUID, Decimal | None]:
        """商品 → 当前单品货品成本（7e R3 毛利，默 12；读取时现算，改价立即生效）。

        = 启用成员 ``goods_style_item.single_goods_cost`` 之和，同商品列表的 ``total_cost``。
        任一启用成员没有成本、或没有启用成员 → None（不当 0）；不在返回里的商品按 None 处理。
        """
        if not goods_ids:
            return {}
        sql = text(
            """
            SELECT gi.goods_main_id AS goods_id,
                   SUM(gi.single_goods_cost) AS unit_cost,
                   COUNT(*) FILTER (WHERE gi.single_goods_cost IS NULL) AS missing
            FROM goods_style_item gi
            WHERE gi.tenant_id = :tenant_id
              AND gi.is_active = true
              AND gi.goods_main_id = ANY(CAST(:goods_ids AS uuid[]))
            GROUP BY gi.goods_main_id
            """
        )
        rows = (
            await self._s.execute(sql, {"tenant_id": tenant_id, "goods_ids": list(goods_ids)})
        ).mappings()
        return {
            r["goods_id"]: (None if int(r["missing"]) > 0 else Decimal(str(r["unit_cost"])))
            for r in rows
        }

    async def daily_trend_by_goods(
        self,
        *,
        tenant_id: UUID,
        goods_id: UUID | None,
        date_from: date,
        date_to: date,
        granularity: str = "day",
        exclude_brushing: bool = True,
    ) -> list[Mapping[str, Any]]:
        """按日/周/月/年汇总投产指标，与投产主表保持相同口径。

        ``goods_id`` 为 ``None`` 时不限商品，按 (商品, 渠道, 日期桶) 分组返回 ——
        这是 ``product_roi_summary`` 汇总表的**唯一刷新源**。渠道（7e R2）：销售 /
        退款 / 加购 / 支付单数按千牛链接的 ``channel``（兜底记普通），站内花费与站内
        加购按万相台链接的 ``channel``，推广与刷单记普通。``goods_id`` 给定时把渠道
        合计成一行（R2 期间投产不拆渠道），形态与改动前一致。``paired_*`` 三列是
        逐日配对加购（``_PAIRED_CART_CTE``），粗桶 = 桶内逐日配对结果之和。

        刷新与实时读共用这一个方法是刻意的：汇总表最大的风险是「预聚合的口径和
        实时查询的口径悄悄分叉」，而这个项目已经为「同一个值两处定义」付过三次代价
        （催发阈值、like_count 双字段、列表接口手写列白名单）。只要刷新不另写一份
        SQL，就不会出现汇总表和实时聚合对不上的情况。
        """
        sales_bucket = bucket_expr(granularity, "q.date")
        ad_bucket = bucket_expr(granularity, "a.date")
        promo_bucket = bucket_expr(granularity, "p.cooperation_date")
        brushing_bucket = bucket_expr(granularity, "oa.order_date")

        # goods_id=None → 全商品，多带一个 goods_id 维度。
        # 四个子查询各自的「归到哪个商品」逻辑不同（销售走 qn_link、广告走
        # platform_product、推广走 goods_main_id 回落主商品、刷单走主商品推定），
        # 所以每段都要单独表达，不能统一加一句 GROUP BY。
        single = goods_id is not None
        g_sel_sales = "" if single else "g.id AS goods_id,"
        g_grp_sales = "" if single else ", g.id"
        g_where_sales = "AND g.id = :goods_id" if single else ""

        g_sel_ads = "" if single else "mapped_a.goods_main_id AS goods_id,"
        g_grp_ads = "" if single else ", mapped_a.goods_main_id"

        g_sel_promo = "" if single else "promo_goods.goods_main_id AS goods_id,"
        g_grp_promo = "" if single else ", promo_goods.goods_main_id"

        g_sel_brush = "" if single else "brush_goods.goods_main_id AS goods_id,"
        g_grp_brush = "" if single else ", brush_goods.goods_main_id"

        g_sel_union = "" if single else "goods_id,"
        g_grp_union = "" if single else ", goods_id"
        g_zero = "" if single else "goods_id,"

        # 渠道维度只在全商品（刷新源）时带；单商品趋势把渠道合计成一行。
        # 推广、刷单没有链接，记普通（设计 3.1）。
        by_channel = not single
        ch_sel_sales = "qn_link.channel AS channel," if by_channel else ""
        ch_grp_sales = ", qn_link.channel" if by_channel else ""
        ch_sel_ads = "mapped_a.channel AS channel," if by_channel else ""
        ch_grp_ads = ", mapped_a.channel" if by_channel else ""
        ch_sel_default = "CAST('普通' AS varchar(16)) AS channel," if by_channel else ""
        ch_col = "channel," if by_channel else ""
        ch_grp = ", channel" if by_channel else ""
        order_by = ("" if single else "goods_id, ") + ("channel, " if by_channel else "") + "d"
        # 配对加购先在 _PAIRED_CART_CTE 里逐日判断，这里只按桶求和
        paired_bucket = bucket_expr(granularity, "pc.d")
        g_sel_paired = "" if single else "pc.goods_id AS goods_id,"
        g_grp_paired = "" if single else ", pc.goods_id"
        ch_sel_paired = "pc.channel AS channel," if by_channel else ""
        ch_grp_paired = ", pc.channel" if by_channel else ""
        sql = text(
            f"""
            WITH {_QIANNIU_GOODS_LINK_CTE}, {_PAIRED_CART_CTE},
            paired AS (
              SELECT {g_sel_paired} {ch_sel_paired} {paired_bucket} AS d,
                     SUM(pc.paired_all_cart) AS paired_all_cart,
                     SUM(pc.paired_ad_cart) AS paired_ad_cart,
                     SUM(pc.paired_days) AS paired_days
              FROM paired_cart pc
              {"WHERE pc.goods_id = :goods_id" if single else ""}
              GROUP BY {paired_bucket}{g_grp_paired}{ch_grp_paired}
            ),
            sales AS (
              SELECT {g_sel_sales} {ch_sel_sales} {sales_bucket} AS d,
                     COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
                     -- 两个 extra 表达式与 aggregate_by_goods 共用同一份常量
                     {_REFUND_SUM} AS refund_amount,
                     {_ADD_CART_SUM} AS add_cart_count,
                     COALESCE(SUM(q.pay_orders), 0) AS pay_orders
              -- 映射复用 qn_link，与 aggregate_by_goods 同一份定义。
              -- 这里原来内联了一份一模一样的 EXISTS/NOT EXISTS/COUNT(DISTINCT)，
              -- 两处各写一遍就是口径漂移的温床 —— 改一边忘一边，趋势图和主表
              -- 会对不上，而且不会报错。
              FROM goods_main g
              JOIN qn_link ON qn_link.goods_id = g.id
              JOIN qianniu_daily q ON q.id = qn_link.qid
              WHERE g.tenant_id = :tenant_id {g_where_sales}
              GROUP BY {sales_bucket}{g_grp_sales}{ch_grp_sales}
            ),
            brushing AS (
              SELECT {g_sel_brush} {ch_sel_default} {brushing_bucket} AS d,
                     COALESCE(SUM(oa.amount), 0) AS brushing_amount
              FROM order_adjustment oa
              -- 同一笔调整只归「主商品」（非套装优先、货号次之），不重复计入
              -- 套装与单品两处。与 aggregate_by_goods 同口径。
              CROSS JOIN LATERAL (
                SELECT gi.goods_main_id FROM goods_style_item gi
                JOIN goods_main gg ON gg.id = gi.goods_main_id
                WHERE gi.style_id = oa.style_id AND gi.is_active = true
                ORDER BY gg.is_suit, gg.goods_code
                LIMIT 1
              ) brush_goods
              -- 刷单额**总是**算出来，减不减由下面的 aggregated 按
              -- :exclude_brushing 决定。这样同一次查询既给出「含刷单」的
              -- pay_amount，也给出可独立存盘的 brushing_amount ——
              -- 汇总表存这两个值就能还原两种口径，不必为开关存两份行。
              WHERE oa.tenant_id = :tenant_id
                -- 只看 exclude_from_roi：标记本身就是「要不要进投产口径」的开关
                AND oa.exclude_from_roi = true
                AND oa.order_date BETWEEN :date_from AND :date_to
                {"AND brush_goods.goods_main_id = :goods_id" if single else ""}
              GROUP BY {brushing_bucket}{g_grp_brush}
            ),
            ads AS (
              SELECT {g_sel_ads} {ch_sel_ads} {ad_bucket} AS d,
                     COALESCE(SUM(a.cost), 0) AS ad_spend,
                     -- 站内加购读 062 的 typed 列（导入时宽松解析，NULL 不计）
                     COALESCE(SUM(a.add_cart_count), 0) AS ad_add_cart_count
              FROM ad_daily a
              JOIN platform_product mapped_a
                ON mapped_a.tenant_id = a.tenant_id
               AND mapped_a.platform = '万相台'
               AND mapped_a.goods_main_id IS NOT NULL
               -- 与 aggregate_by_goods 同口径：停用链接不进单品（7e-9）
               AND mapped_a.is_active = true
               AND (
                 (a.platform_product_id IS NOT NULL
                  AND mapped_a.id = a.platform_product_id)
                 OR (a.platform_product_id IS NULL
                     AND mapped_a.platform_id = a.platform_id_snapshot)
               )
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
                {"AND mapped_a.goods_main_id = :goods_id" if single else ""}
              GROUP BY {ad_bucket}{g_grp_ads}{ch_grp_ads}
            ),
            promos AS (
              SELECT {g_sel_promo} {ch_sel_default} {promo_bucket} AS d,
                     -- 站外推广成本口径，与投产报表一致（PRD V1.4 §9）
                     COALESCE(SUM(p.total_promo_cost), 0) AS promo_cost
              FROM promotion p
              -- 先看推广记录自己的商品归属，为空才回落到主商品推定。
              -- 与 aggregate_by_goods 同口径。
              CROSS JOIN LATERAL (
                SELECT COALESCE(p.goods_main_id, (
                  SELECT gi.goods_main_id FROM goods_style_item gi
                  JOIN goods_main gg ON gg.id = gi.goods_main_id
                  WHERE gi.style_id = p.style_id AND gi.is_active = true
                  ORDER BY gg.is_suit, gg.goods_code
                  LIMIT 1
                )) AS goods_main_id
              ) promo_goods
              WHERE p.tenant_id = :tenant_id
                AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
                AND promo_goods.goods_main_id IS NOT NULL
                {"AND promo_goods.goods_main_id = :goods_id" if single else ""}
              GROUP BY {promo_bucket}{g_grp_promo}
            ),
            aggregated AS (
              SELECT {g_sel_union} {ch_col} d,
                     -- pay_amount 的语义不变（按开关扣减），额外把 brushing_amount
                     -- 原样带出来给汇总表存。
                     COALESCE(SUM(pay_amount), 0)
                       - CASE WHEN :exclude_brushing
                              THEN COALESCE(SUM(brushing_amount), 0)
                              ELSE 0 END AS pay_amount,
                     COALESCE(SUM(brushing_amount), 0) AS brushing_amount,
                     COALESCE(SUM(refund_amount), 0) AS refund_amount,
                     COALESCE(SUM(add_cart_count), 0) AS add_cart_count,
                     COALESCE(SUM(pay_orders), 0) AS pay_orders,
                     COALESCE(SUM(promo_cost), 0) AS promo_cost,
                     COALESCE(SUM(ad_spend), 0) AS ad_spend,
                     COALESCE(SUM(ad_add_cart_count), 0) AS ad_add_cart_count,
                     COALESCE(SUM(paired_all_cart), 0) AS paired_all_cart,
                     COALESCE(SUM(paired_ad_cart), 0) AS paired_ad_cart,
                     COALESCE(SUM(paired_days), 0) AS paired_days
              FROM (
                SELECT {g_zero} {ch_col} d, pay_amount, refund_amount, add_cart_count,
                       pay_orders,
                       0::numeric AS brushing_amount,
                       0::numeric AS promo_cost, 0::numeric AS ad_spend,
                       0::bigint AS ad_add_cart_count,
                       0::bigint AS paired_all_cart, 0::bigint AS paired_ad_cart,
                       0::bigint AS paired_days
                FROM sales
                UNION ALL
                SELECT {g_zero} {ch_col} d, 0, 0, 0, 0, brushing_amount, 0, 0, 0, 0, 0, 0
                FROM brushing
                UNION ALL
                SELECT {g_zero} {ch_col} d, 0, 0, 0, 0, 0, 0, ad_spend, ad_add_cart_count,
                       0, 0, 0
                FROM ads
                UNION ALL
                SELECT {g_zero} {ch_col} d, 0, 0, 0, 0, 0, promo_cost, 0, 0, 0, 0, 0
                FROM promos
                UNION ALL
                SELECT {g_zero} {ch_col} d, 0, 0, 0, 0, 0, 0, 0, 0,
                       paired_all_cart, paired_ad_cart, paired_days
                FROM paired
              ) source
              GROUP BY d{g_grp_union}{ch_grp}
            ),
            calculated AS (
              SELECT {g_sel_union} {ch_col} d, pay_amount, brushing_amount, refund_amount,
                     add_cart_count, pay_orders,
                     pay_amount - refund_amount AS confirmed_amount,
                     promo_cost, ad_spend, ad_add_cart_count,
                     paired_all_cart, paired_ad_cart, paired_days,
                     promo_cost + ad_spend AS total_spend
              FROM aggregated
            )
            SELECT {g_sel_union} {ch_col} d AS date, pay_amount, brushing_amount,
                   refund_amount, add_cart_count, pay_orders, confirmed_amount,
                   promo_cost, ad_spend, ad_add_cart_count,
                   paired_all_cart, paired_ad_cart, paired_days, total_spend,
                   -- 与 services.metric.common.safe_div 同口径：分母 ≤ 0 置 NULL
                   CASE WHEN total_spend <= 0 THEN NULL
                        ELSE ROUND(confirmed_amount / total_spend, 4)
                   END AS net_roi
            FROM calculated
            ORDER BY {order_by}
            """
        )
        return as_mappings(
            (
                await self._s.execute(
                    sql,
                    {
                        "tenant_id": tenant_id,
                        "goods_id": goods_id,
                        "date_from": date_from,
                        "date_to": date_to,
                        "exclude_brushing": exclude_brushing,
                    },
                )
            )
            .mappings()
            .all()
        )

    async def fetch_extra_by_goods(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """按商品拉取千牛/站内 extra，显式 ID 优先并限制平台，避免重复归集。

        套装的成员款式共用一条链接，归集到同一个商品后 extra 只累加一次；
        原先按款式归集时同一份 extra 会被每个成员款式各算一遍。
        """
        sql = text(
            """
            SELECT pp.goods_main_id AS goods_id, q.extra AS extra, 'qianniu' AS src
            FROM qianniu_daily q
            JOIN platform_product pp
              ON pp.tenant_id = q.tenant_id
              AND pp.platform = '千牛'
              AND pp.goods_main_id IS NOT NULL
              -- 停用链接不进单品（7e-9）；下面兜底的 NOT EXISTS 不看 is_active
              AND pp.is_active = true
              AND (
                (q.platform_product_id IS NOT NULL
                 AND pp.id = q.platform_product_id)
                OR (q.platform_product_id IS NULL
                    AND pp.platform_id = q.platform_id_snapshot)
              )
            WHERE q.tenant_id = :tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND q.extra IS NOT NULL
            UNION ALL
            -- 套装走兜底路径时每个成员款式都会命中，但它们指向同一个商品。
            -- 按 (日报, 商品) 去重，否则同一份 extra 会被累加多次。
            SELECT goods_id, extra, 'qianniu' AS src
            FROM (
              SELECT DISTINCT q.id AS daily_id,
                     li.goods_main_id AS goods_id,
                     q.extra AS extra
              FROM qianniu_daily q
              JOIN style s
                ON s.tenant_id = q.tenant_id
                AND s.is_deleted = false
                AND s.qianniu_product_id IS NOT NULL
                AND s.qianniu_product_id = q.platform_id_snapshot
              JOIN goods_style_item li ON li.style_id = s.id AND li.is_active = true
              WHERE q.tenant_id = :tenant_id
                AND q.date BETWEEN :date_from AND :date_to
                AND q.extra IS NOT NULL
                AND q.platform_product_id IS NULL
                AND NOT EXISTS (
                  SELECT 1 FROM platform_product pp
                  WHERE pp.tenant_id = q.tenant_id
                    AND pp.platform = '千牛'
                    AND pp.platform_id = q.platform_id_snapshot
                )
                AND (
                  SELECT COUNT(DISTINCT li2.goods_main_id)
                  FROM style legacy_s
                  JOIN goods_style_item li2
                    ON li2.style_id = legacy_s.id AND li2.is_active = true
                  WHERE legacy_s.tenant_id = q.tenant_id
                    AND legacy_s.is_deleted = false
                    AND legacy_s.qianniu_product_id = q.platform_id_snapshot
                ) = 1
            ) legacy_extra
            UNION ALL
            SELECT pp.goods_main_id AS goods_id, a.extra AS extra, 'ad' AS src
            FROM ad_daily a
            JOIN platform_product pp
              ON pp.tenant_id = a.tenant_id
              AND pp.platform = '万相台'
              AND pp.goods_main_id IS NOT NULL
              AND pp.is_active = true
              AND (
                (a.platform_product_id IS NOT NULL
                 AND pp.id = a.platform_product_id)
                OR (a.platform_product_id IS NULL
                    AND pp.platform_id = a.platform_id_snapshot)
              )
            WHERE a.tenant_id = :tenant_id
              AND a.date BETWEEN :date_from AND :date_to
              AND a.extra IS NOT NULL
            """
        )
        return as_mappings(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .all()
        )


class BiRepository:
    """TASK 15 BI 看板专用聚合，统一合作日期、状态与租户口径。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_store_summary(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> Mapping[str, Any]:
        sql = text(
            f"""
            WITH sales AS (
              SELECT COALESCE(SUM(q.pay_amount), 0) AS sales_amount,
                     {_REFUND_SUM} AS refund_amount
              FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id
                AND q.date BETWEEN :date_from AND :date_to
            ), ads AS (
              SELECT COALESCE(SUM(a.cost), 0) AS internal_spend
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
            ), promos AS (
              -- 站外推广成本口径（PRD V1.4 §9），不是约稿金额
              SELECT COALESCE(SUM(p.total_promo_cost), 0) AS external_spend
              FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
            )
            SELECT sales.sales_amount, sales.refund_amount,
                   ads.internal_spend, promos.external_spend
            FROM sales CROSS JOIN ads CROSS JOIN promos
            """
        )
        return as_mapping(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .one()
        )

    async def aggregate_promotion_summary(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> Mapping[str, Any]:
        """约稿金额汇总。

        这里刻意用 ``quote_amount`` 而不是 ``total_promo_cost``：PRD V1.4 §9 定义
        「小红书约篇金额 = 博主服务费合计」，是给博主的钱，不含样品成本与寄回运费。
        站外推广成本是另一个口径（见 ``aggregate_by_goods`` 的 promo 子查询），
        两者不要混。
        """
        sql = text(
            """
            SELECT
              COALESCE(SUM(p.quote_amount), 0) AS commission_amount,
              COUNT(*) AS commission_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '已发布'), 0) AS published_spend,
              COUNT(*) FILTER (WHERE p.publish_status = '已发布') AS published_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '未发布'), 0) AS unpublished_spend,
              COUNT(*) FILTER (WHERE p.publish_status = '未发布') AS unpublished_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '已取消'), 0) AS cancelled_amount,
              COUNT(*) FILTER (WHERE p.publish_status = '已取消') AS cancelled_count
            FROM promotion p
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            """
        )
        return as_mapping(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .one()
        )

    async def aggregate_workload(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        today: date,
        thresholds: UrgeThresholds,
    ) -> list[Mapping[str, Any]]:
        # 这里只数「超时」，与两个阈值无关；传租户配置只是为了不再留一份写死的 10 / 3
        sql = text(
            f"""
            WITH work AS (
              SELECT p.pr_id,
                     COALESCE(u.display_name, u.username, '未分配') AS pr_name,
                     COUNT(*) AS quote_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '已发布') AS publish_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '未发布') AS pending_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '已取消') AS cancel_count,
                     COUNT(*) FILTER (WHERE ({_URGE}) = '超时') AS overdue_count
              FROM promotion p
              LEFT JOIN "user" u ON u.id = p.pr_id
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY p.pr_id, u.display_name, u.username
            ), targets AS (
              SELECT t.pr_id, COALESCE(SUM(t.min_target), 0) AS target_count
              FROM target_planning t
              WHERE t.tenant_id = :tenant_id
                AND t.period_month BETWEEN
                    to_char(CAST(:date_from AS date), 'YYYY-MM') AND
                    to_char(CAST(:date_to AS date), 'YYYY-MM')
              GROUP BY t.pr_id
            )
            SELECT work.*, COALESCE(targets.target_count, 0) AS target_count
            FROM work
            LEFT JOIN targets ON targets.pr_id = work.pr_id
            ORDER BY work.quote_count DESC, work.pr_name
            """
        )
        params = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            "today": today,
            **thresholds.sql_params(),
        }
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    async def aggregate_trend(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        granularity: str,
    ) -> list[Mapping[str, Any]]:
        q_bucket = bucket_expr(granularity, "q.date")
        a_bucket = bucket_expr(granularity, "a.date")
        p_bucket = bucket_expr(granularity, "p.cooperation_date")
        sql = text(
            f"""
            SELECT d AS date,
                   COALESCE(SUM(sales_amount), 0) AS sales_amount,
                   COALESCE(SUM(refund_amount), 0) AS refund_amount,
                   COALESCE(SUM(internal_spend), 0) AS internal_spend,
                   COALESCE(SUM(external_spend), 0) AS external_spend
            FROM (
              SELECT {q_bucket} AS d,
                     COALESCE(SUM(q.pay_amount), 0) AS sales_amount,
                     {_REFUND_SUM} AS refund_amount,
                     0::numeric AS internal_spend, 0::numeric AS external_spend
              FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id
                AND q.date BETWEEN :date_from AND :date_to
              GROUP BY {q_bucket}
              UNION ALL
              SELECT {a_bucket} AS d, 0, 0,
                     COALESCE(SUM(a.cost), 0), 0
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
              GROUP BY {a_bucket}
              UNION ALL
              SELECT {p_bucket} AS d, 0, 0, 0,
                     -- 站外推广成本口径（PRD V1.4 §9）
                     COALESCE(SUM(p.total_promo_cost), 0)
              FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY {p_bucket}
            ) source
            GROUP BY d
            ORDER BY d
            """
        )
        return as_mappings(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .all()
        )

    async def published_spend_by_goods(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """已发布推广费按商品归集，与 aggregate_by_goods 的 promo 子查询同口径。"""
        sql = text(
            """
            SELECT COALESCE(p.goods_main_id, owner.goods_main_id) AS goods_id,
                   -- 站外推广成本口径（PRD V1.4 §9）
                   COALESCE(SUM(p.total_promo_cost), 0) AS external_spend
            FROM promotion p
            LEFT JOIN LATERAL (
              SELECT gi.goods_main_id
              FROM goods_style_item gi
              JOIN goods_main gg ON gg.id = gi.goods_main_id
              WHERE gi.style_id = p.style_id AND gi.is_active = true
              ORDER BY gg.is_suit, gg.goods_code
              LIMIT 1
            ) owner ON p.goods_main_id IS NULL
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.publish_status = '已发布'
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            GROUP BY COALESCE(p.goods_main_id, owner.goods_main_id)
            """
        )
        return as_mappings(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            )
            .mappings()
            .all()
        )


@dataclass(frozen=True)
class DrillFilters:
    """下钻的筛选条件（已校验）。``pr_scope`` 由 ``resolve_pr_scope`` 定，下推到 SQL。"""

    tenant_id: UUID
    date_from: date
    date_to: date
    pr_scope: PrScope
    goods_id: UUID | None
    seasons: Sequence[str] | None
    publish_status: str | None


class DrillRepository:
    """报表下钻的推广单明细（7e R1，报表视图设计 2.1）。

    只取未删除（``is_active``）的单，按合作日期筛；商品归属与投产同一口径
    （``_PROMO_GOODS_LATERAL``），品名用推广模块的简称规则、商品按归属 JOIN。
    投影是白名单（见 ``DrillPromotionRow``），收件信息 / 收款码 / 备注 / ``source_extra`` 不进 SELECT。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _where(f: DrillFilters) -> tuple[str, dict[str, Any]]:
        scope_sql, scope_params = f.pr_scope.sql("p.pr_id")
        season_sql, season_params = goods_filter_clauses(f.seasons)
        clauses = [
            "p.tenant_id = :tenant_id AND p.is_active = true",
            "AND p.cooperation_date BETWEEN :date_from AND :date_to",
            scope_sql,
            season_sql,
        ]
        params: dict[str, Any] = {
            "tenant_id": f.tenant_id,
            "date_from": f.date_from,
            "date_to": f.date_to,
            **scope_params,
            **season_params,
        }
        if f.goods_id is not None:
            clauses.append(f"AND {_PROMO_GOODS_ID} = CAST(:goods_id AS uuid)")
            params["goods_id"] = f.goods_id
        if f.publish_status is not None:
            clauses.append("AND p.publish_status = CAST(:publish_status AS text)")
            params["publish_status"] = f.publish_status
        return "\n".join(c for c in clauses if c), params

    _FROM = f"""
        FROM promotion p
        {_PROMO_GOODS_LATERAL}
        LEFT JOIN goods_main g
          ON g.id = {_PROMO_GOODS_ID} AND g.tenant_id = p.tenant_id
        """

    async def count(self, filters: DrillFilters) -> int:
        where, params = self._where(filters)
        sql = text(f"SELECT COUNT(*) {self._FROM} WHERE {where}")
        return int((await self._s.execute(sql, params)).scalar_one())

    async def page(
        self, filters: DrillFilters, *, limit: int, offset: int
    ) -> list[Mapping[str, Any]]:
        where, params = self._where(filters)
        sql = text(
            f"""
            SELECT
              p.id AS id, p.internal_code AS internal_code,
              p.cooperation_date AS cooperation_date,
              p.scheduled_publish_date AS scheduled_publish_date,
              p.actual_publish_date AS actual_publish_date,
              p.pr_id AS pr_id,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              b.nickname AS blogger_nickname,
              p.platform AS platform,
              {display_short_name_sql(promotion="p", goods="g")} AS display_short_name,
              p.publish_status AS publish_status, p.recall_status AS recall_status,
              p.quote_amount AS quote_amount, p.total_promo_cost AS total_promo_cost,
              p.like_count AS like_count
            {self._FROM}
            JOIN blogger b ON b.id = p.blogger_id
            LEFT JOIN "user" u ON u.id = p.pr_id
            WHERE {where}
            ORDER BY p.cooperation_date DESC, p.internal_code, p.id
            LIMIT CAST(:limit AS int) OFFSET CAST(:offset AS int)
            """
        )
        params = {**params, "limit": limit, "offset": offset}
        return as_mappings((await self._s.execute(sql, params)).mappings().all())


async def style_exists(session: AsyncSession, tenant_id: UUID, style_id: UUID) -> bool:
    sql = text("SELECT 1 FROM style WHERE id = :sid AND tenant_id = :tid LIMIT 1")
    return (
        await session.execute(sql, {"sid": style_id, "tid": tenant_id})
    ).scalar_one_or_none() is not None


__all__ = [
    "GOODS_META_COLUMNS",
    "PRODUCTION_ADDITIVE_COLUMNS",
    "PRODUCTION_COUNT_COLUMNS",
    "PRODUCTION_MONEY_COLUMNS",
    "BiRepository",
    "DrillFilters",
    "DrillRepository",
    "ProductionRepository",
    "StoreDailyRepository",
    "TargetPlanningRepository",
    "WorkProgressRepository",
    "bucket_expr",
    "goods_channel_select",
    "goods_filter_clauses",
    "style_exists",
]
