"""推广单派生「当前阶段」（流程线 3.8，7d-13）。

不落库：一张有序规则表 ``STAGE_RULES``，两种渲染——
- ``compute_stage()``：Python 版，按顺序取第一条为真的（详情、矩阵快照用）
- ``stage_sql_expr()``：SQL 版，同一组片段按同一顺序拼成 ``CASE WHEN … END``（列表 CTE 用）

顺序和阶段名只有一份，两边不会漂；``tests/integration/test_stage_sql_parity.py`` 穷举对拍守着。
第 8 条（未发布 / 异常）直接嵌 ``calculate_urge_status`` / ``URGE_STATUS_SQL_EXPR``，
SQL 的绑定参数沿用它的 ``:today`` / ``:urge_days`` / ``:important_days``。

PR-6 覆盖 #1、#1a、#4 ~ #16；还没有 ``completed_at``（M5，PR-7）前按两条过渡规则：
- 已取消 + 召回成功 → 「已完结 · 召回」（#2 的过渡）
- #16 之后：已结款 且 已录 7 天数据 且 复盘 = 已完成 → 「已完结」（#3 的过渡，D8），
  免得存量已完成的单落「状态异常」；复盘「待确认」算 ≠ 已完成
- 发布日为空时 #14「待满 7 天」不拦（与录数的 7 天门槛同口径）
- 其余（如已发布却还是未核查）→ 状态异常

SQL 片段引用的列名不带表前缀（``publish_status`` 等）：放在只有推广单列的一层里用，
``style`` 也有 ``is_active``，直接放进 JOIN 了款式的 SELECT 会歧义。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from app.modules.promotion.urge_calculator import (
    URGE_STATUS_SQL_EXPR,
    UrgeThresholds,
    calculate_urge_status,
)

URGE_STAGES: tuple[str, ...] = ("档期内", "催发", "重要催发", "超时", "未排期")
"""第 8 条展开出的 5 个阶段名（= ``calculate_urge_status`` 对未发布 / 异常的取值）。"""

STAGE_FALLBACK = "状态异常"
"""第 17 条「其余」。正常走不到，列表上能筛出来排查。"""

PROMOTION_STAGES: tuple[str, ...] = (
    "待推送仓库",
    "待仓库发货",
    *URGE_STAGES,
    "待主管审核",
    "推广驳回 · 待重提",
    "待财务付款",
    "结款驳回 · 待重提",
    "待传结款截图",
    "待满 7 天",
    "待录 7 天数据",
    "待复盘",
    "召回中",
    "已完结",
    "已完结 · 召回",
    "已完结 · 不合作",
    "已停用",
    "已删除",
    STAGE_FALLBACK,
)
"""3.8 的 22 个阶段名（矩阵 ``MATRICES["promotion"]`` 的阶段与这里同一份），按 A ~ H 列排。"""

STAGE_COLUMN: dict[str, str] = {
    "待推送仓库": "A",
    "待仓库发货": "B",
    **dict.fromkeys(URGE_STAGES, "C"),
    "待主管审核": "D",
    "推广驳回 · 待重提": "D",
    "待财务付款": "E",
    "结款驳回 · 待重提": "E",
    "待传结款截图": "E",
    "待满 7 天": "F",
    "待录 7 天数据": "F",
    "待复盘": "F",
    "召回中": "G",
    "已完结": "H",
    "已完结 · 召回": "H",
    "已完结 · 不合作": "H",
    "已停用": "H",
    "已删除": "H",
    STAGE_FALLBACK: "H",
}
"""阶段 → 矩阵列（5.3 的 A ~ H，``ui.column``）。"""


METRICS_WAIT_DAYS = 7
"""发文满几天才能录 7 天数据（3.7）：``今天 ≥ 发布日 + 7``（Asia/Shanghai 的 ``get_today()``）。"""


def metrics_available_from(actual_publish_date: date | None) -> date | None:
    """7 天数据可以录的第一天；没有发布日 → ``None``（门槛不拦，D8）。"""
    if actual_publish_date is None:
        return None
    return actual_publish_date + timedelta(days=METRICS_WAIT_DAYS)


def seven_days_reached(actual_publish_date: date | None, today: date) -> bool:
    """发文已满 7 天；没有发布日按满（门槛不拦）。"""
    available = metrics_available_from(actual_publish_date)
    return available is None or today >= available


@dataclass(frozen=True)
class StageFacts:
    """算阶段要用到的离散输入（PR-7 补完结 ``completed_at``）。"""

    publish_status: str
    recall_status: str
    settlement_status: str
    ship_status: str | None
    is_active: bool
    retro_status: str
    metrics_recorded: bool
    completed: bool
    seven_days_reached: bool


class StageRule(NamedTuple):
    """一条规则：``name`` 为 ``BY_URGE`` 时阶段名取催发状态（第 8 条）。"""

    name: str
    py: Callable[[StageFacts], bool]
    sql: str


BY_URGE = "<催发状态>"
"""第 8 条的占位名：Python 取 ``calculate_urge_status``，SQL 取 ``URGE_STATUS_SQL_EXPR``。"""

_UNPUBLISHED = frozenset({"未发布", "异常"})
_UNPUBLISHED_SQL = "publish_status IN ('未发布', '异常')"
_SETTLED_SQL = "settlement_status = '已结款'"


def _settled(f: StageFacts) -> bool:
    return f.settlement_status == "已结款"


STAGE_RULES: tuple[StageRule, ...] = (
    # 1
    StageRule("已删除", lambda f: f.publish_status == "已删除", "publish_status = '已删除'"),
    # 1a 停用（11-15）
    StageRule("已停用", lambda f: not f.is_active, "is_active = false"),
    # 2 / 3：completed_at是唯一完结标记，优先于召回和结款阶段。
    StageRule(
        "已完结 · 召回",
        lambda f: f.completed and f.publish_status == "已取消",
        "completed_at IS NOT NULL AND publish_status = '已取消'",
    ),
    StageRule("已完结", lambda f: f.completed, "completed_at IS NOT NULL"),
    # 4
    StageRule("召回中", lambda f: f.recall_status == "召回中", "recall_status = '召回中'"),
    # 5
    StageRule(
        "已完结 · 不合作",
        lambda f: f.publish_status == "已取消" and f.recall_status in {"未召回", "召回失败"},
        "publish_status = '已取消' AND recall_status IN ('未召回', '召回失败')",
    ),
    # 6
    StageRule(
        "待推送仓库",
        lambda f: f.publish_status in _UNPUBLISHED and f.ship_status == "待发货",
        f"{_UNPUBLISHED_SQL} AND ship_status = '待发货'",
    ),
    # 7
    StageRule(
        "待仓库发货",
        lambda f: f.publish_status in _UNPUBLISHED and f.ship_status == "待打单",
        f"{_UNPUBLISHED_SQL} AND ship_status = '待打单'",
    ),
    # 8
    StageRule(BY_URGE, lambda f: f.publish_status in _UNPUBLISHED, _UNPUBLISHED_SQL),
    # 9
    StageRule(
        "待主管审核",
        lambda f: f.settlement_status == "待核查",
        "settlement_status = '待核查'",
    ),
    # 10
    StageRule(
        "推广驳回 · 待重提",
        lambda f: f.settlement_status == "已驳回",
        "settlement_status = '已驳回'",
    ),
    # 11
    StageRule(
        "待财务付款",
        lambda f: f.settlement_status == "待付款",
        "settlement_status = '待付款'",
    ),
    # 12
    StageRule(
        "结款驳回 · 待重提",
        lambda f: f.settlement_status == "结款驳回",
        "settlement_status = '结款驳回'",
    ),
    # 13
    StageRule(
        "待传结款截图",
        lambda f: f.settlement_status == "已付款",
        "settlement_status = '已付款'",
    ),
    # 14 发布日为空不拦（D8）
    StageRule(
        "待满 7 天",
        lambda f: _settled(f) and not f.metrics_recorded and not f.seven_days_reached,
        f"{_SETTLED_SQL} AND metrics_recorded_at IS NULL AND actual_publish_date IS NOT NULL"
        f" AND CAST(:today AS date) < actual_publish_date + {METRICS_WAIT_DAYS}",
    ),
    # 15
    StageRule(
        "待录 7 天数据",
        lambda f: _settled(f) and not f.metrics_recorded,
        f"{_SETTLED_SQL} AND metrics_recorded_at IS NULL",
    ),
    # 16（复盘「待确认」也算 ≠ 已完成）
    StageRule(
        "待复盘",
        lambda f: _settled(f) and f.retro_status != "已完成",
        f"{_SETTLED_SQL} AND retro_status <> '已完成'",
    ),
)


def compute_stage(
    *,
    publish_status: str,
    recall_status: str,
    settlement_status: str,
    ship_status: str | None,
    is_active: bool,
    retro_status: str,
    metrics_recorded_at: datetime | None,
    completed_at: datetime | None,
    actual_publish_date: date | None,
    scheduled_publish_date: date | None,
    today: date,
    thresholds: UrgeThresholds,
) -> str:
    """Python 版：自上而下取第一条命中的规则；都不中 → ``STAGE_FALLBACK``。"""
    facts = StageFacts(
        publish_status=publish_status,
        recall_status=recall_status,
        settlement_status=settlement_status,
        ship_status=ship_status,
        is_active=is_active,
        retro_status=retro_status,
        metrics_recorded=metrics_recorded_at is not None,
        completed=completed_at is not None,
        seven_days_reached=seven_days_reached(actual_publish_date, today),
    )
    for rule in STAGE_RULES:
        if not rule.py(facts):
            continue
        if rule.name == BY_URGE:
            return calculate_urge_status(
                publish_status=publish_status,
                scheduled_publish_date=scheduled_publish_date,
                today=today,
                urge_threshold_days=thresholds.urge_days,
                important_threshold_days=thresholds.important_days,
            )
        return rule.name
    return STAGE_FALLBACK


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def stage_sql_expr() -> str:
    """SQL 版：同一张规则表拼成 ``CASE WHEN … THEN … ELSE '状态异常' END``。

    绑定参数同 ``URGE_STATUS_SQL_EXPR``（``:today`` / ``:urge_days`` / ``:important_days``，
    ``UrgeThresholds.sql_params()``）；依赖列 ``publish_status``、``recall_status``、
    ``settlement_status``、``ship_status``、``is_active``、``scheduled_publish_date``、
    ``retro_status``、``metrics_recorded_at``、``actual_publish_date``。
    """
    # 嵌进来的催发 CASE 多缩进一层，顶层 WHEN 一眼能数
    urge = URGE_STATUS_SQL_EXPR.strip().replace("\n", "\n    ")
    whens = [
        f"  WHEN {rule.sql} THEN "
        + (f"({urge})" if rule.name == BY_URGE else _sql_literal(rule.name))
        for rule in STAGE_RULES
    ]
    return "CASE\n" + "\n".join(whens) + f"\n  ELSE {_sql_literal(STAGE_FALLBACK)}\nEND"


BADGES: tuple[str, ...] = ("missing_qr", "metrics_due", "metrics_wait_settle", "retro_due")
"""行角标（7.3 badge 行），按这个顺序出。``freight_open``（R33 付款单未结）随 PR-8；
SQL 版与按角标筛选随 PR-10。"""


def compute_badges(
    *,
    stage: str,
    publish_status: str,
    settlement_status: str,
    retro_status: str,
    metrics_recorded_at: datetime | None,
    actual_publish_date: date | None,
    quote_amount: Decimal | None,
    has_payment_qr: bool,
    today: date,
) -> list[str]:
    """不进阶段的派生量（3.8 末尾）：一格只放一个阶段，其余的作角标。

    - ``missing_qr``：待主管审核 且 服务费 > 0 且 没有收款码
    - ``metrics_due``：已结款、未录、满 7 天（可以录了）
    - ``metrics_wait_settle``：已发布、未录、满 7 天、还没结款（灰标「满 7 天 · 待结款」）
    - ``retro_due``：已发布 且 复盘 ≠ 已完成（结款没走完也提示）
    「满 7 天」没有发布日按满（与录数门槛同口径，D8）。
    """
    recorded = metrics_recorded_at is not None
    reached = seven_days_reached(actual_publish_date, today)
    settled = settlement_status == "已结款"
    published = publish_status == "已发布"
    flags = {
        "missing_qr": stage == "待主管审核"
        and (quote_amount or Decimal(0)) > 0
        and not has_payment_qr,
        "metrics_due": settled and not recorded and reached,
        "metrics_wait_settle": published and not settled and not recorded and reached,
        "retro_due": published and retro_status != "已完成",
    }
    return [name for name in BADGES if flags[name]]


__all__ = [
    "BADGES",
    "BY_URGE",
    "METRICS_WAIT_DAYS",
    "PROMOTION_STAGES",
    "STAGE_COLUMN",
    "STAGE_FALLBACK",
    "STAGE_RULES",
    "URGE_STAGES",
    "StageFacts",
    "StageRule",
    "compute_badges",
    "compute_stage",
    "metrics_available_from",
    "seven_days_reached",
    "stage_sql_expr",
]
