"""推广单「品名」的显示规则（7a-8）。SQL 与 Python 两条路径共用这一处。

业务方 10-05：推广列表 / 仓库打单的品名、企微催发模板里的 ``{商品简称}``、催发任务与
博主卡上的款式名，改用**商品简称**（``goods_main.short_name``，056 新加）。

- 商品简称去掉首尾空格后非空 → 显示商品简称
- 否则（没填、全空白、没有归属商品）→ 回落建单时的快照 ``promotion.style_short_name_snapshot``

回落的是快照、不是商品全称：品名一直显示的是快照，上线当天商品简称全是空的，
这些单的品名不能变。快照本身**不回填、不改写** —— 它是建单那一刻的事实，导出、
报表与历史核对都在用。

两条路径必须逐字一致：列表 / 仓库 / 催发 / 博主卡 / 企微扫描走 SQL 片段，详情与各状态
推进后的单条响应走 Python helper。PostgreSQL 的 ``BTRIM(x)`` 只去半角空格，所以 Python
侧用 ``strip(" ")`` 而不是 ``strip()`` —— 否则由全角空格或换行组成的简称在两条路径上
显示得不一样。
"""

from __future__ import annotations


def display_short_name_sql(*, promotion: str = "p", goods: str = "g") -> str:
    """品名的 SQL 表达式。``promotion`` / ``goods`` 是调用方 SQL 里两张表的别名。

    调用方负责 ``LEFT JOIN goods_main <goods> ON <goods>.id = <promotion>.goods_main_id
    AND <goods>.tenant_id = <promotion>.tenant_id``。
    """
    return (
        f"COALESCE(NULLIF(BTRIM({goods}.short_name), ''), "
        f"{promotion}.style_short_name_snapshot)"
    )


PROMOTION_DISPLAY_SHORT_NAME_SQL: str = display_short_name_sql()
"""别名 ``p`` = promotion、``g`` = goods_main 时的品名表达式。"""


def normalize_goods_short_name(value: str | None) -> str | None:
    """商品简称归一：去首尾半角空格后为空 → None。与 SQL 的 ``NULLIF(BTRIM(x), '')`` 一致。"""
    cleaned = (value or "").strip(" ")
    return cleaned or None


def promotion_display_short_name(
    *, goods_short_name: str | None, style_short_name_snapshot: str
) -> str:
    """品名的 Python 实现，与 ``display_short_name_sql`` 逐字一致。"""
    return normalize_goods_short_name(goods_short_name) or style_short_name_snapshot


__all__ = [
    "PROMOTION_DISPLAY_SHORT_NAME_SQL",
    "display_short_name_sql",
    "normalize_goods_short_name",
    "promotion_display_short_name",
]
