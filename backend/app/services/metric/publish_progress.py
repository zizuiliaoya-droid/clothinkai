"""发文进度聚合辅助（U08）。

点赞折算 CASE 表达式由 U04 ``PLATFORM_LIKE_COEFFICIENT`` 动态生成，避免系数硬编码漂移。
聚合 SQL 主体在 ``modules/report/repository.py``；本模块仅提供可复用的 SQL 片段构造。
"""

from __future__ import annotations

from app.modules.promotion.legacy_settings import PLATFORM_LIKE_COEFFICIENT


def like_sum_expr(column: str = "like_count") -> str:
    """生成折算后点赞求和 SQL 片段（COALESCE(SUM(CASE ...),0)）。

    系数 < 1 的平台（抖音/快手 ×0.1）走 CASE 折算，其余按原值；NULL 视作 0。
    """
    discount = {
        p: c for p, c in PLATFORM_LIKE_COEFFICIENT.items() if c < 1
    }
    if not discount:
        return f"COALESCE(SUM({column}), 0)"
    # 同系数平台合并到一个 IN 列表（MVP 抖音/快手 同为 0.1）
    plats = ", ".join(f"'{p}'" for p in discount)
    coef = float(next(iter(discount.values())))
    return (
        f"COALESCE(SUM(CASE WHEN platform IN ({plats}) "
        f"THEN {column} * {coef} ELSE {column} END), 0)"
    )


__all__ = ["like_sum_expr"]
