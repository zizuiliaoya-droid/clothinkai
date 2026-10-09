"""推广单派生阶段：Python ``compute_stage`` 与 SQL ``stage_sql_expr`` 穷举对拍（流程线 3.8）。

PR-2 的离散输入：发布 5 × 召回 4 × 结款 5 × 发货 4（含 NULL）× 启用 2 × 排期相对今天 6 档 = 4,800 组，
每列一个数组参数、``unnest`` 一次算完（不插表）。PR-6 / PR-7 补规则时同步加维度（完结、复盘、已录、发布日）。

场景有效性：PR-2 可达的 17 个阶段名每个至少命中一次，走不到的 5 个恰好没出现——
防止「两边都没走到」的假绿。
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import PublishStatus, RecallStatus, SettlementStatus, ShipStatus
from app.modules.promotion.repository import PromotionListFilters as RepoFilters
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.stage_calculator import (
    PROMOTION_STAGES,
    compute_stage,
    stage_sql_expr,
)
from app.modules.promotion.urge_calculator import UrgeThresholds, get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TH = UrgeThresholds(urge_days=10, important_days=3)

# PR-2 还没有对应状态值 / 列的阶段（PR-6 / PR-7 补规则后可达）
_UNREACHABLE_IN_PR2 = frozenset(
    {"结款驳回 · 待重提", "待传结款截图", "待满 7 天", "待录 7 天数据", "已完结"}
)


def _sched_tiers(today: date) -> list[date | None]:
    """排期 6 档：未排期 / 档期内 / 催发边界 / 重要催发边界 / 当天 / 超时。"""
    return [
        None,
        today + timedelta(days=TH.urge_days + 1),
        today + timedelta(days=TH.urge_days),
        today + timedelta(days=TH.important_days),
        today,
        today - timedelta(days=1),
    ]


def _combos(today: date) -> list[tuple[str, str, str, str | None, bool, date | None]]:
    return list(
        itertools.product(
            [s.value for s in PublishStatus],
            [s.value for s in RecallStatus],
            [s.value for s in SettlementStatus],
            [None, *(s.value for s in ShipStatus)],
            [True, False],
            _sched_tiers(today),
        )
    )


async def _sql_stages(
    session: AsyncSession,
    combos: list[tuple[str, str, str, str | None, bool, date | None]],
    today: date,
) -> list[str]:
    cols = list(zip(*combos, strict=True))
    sql = text(
        f"""
        SELECT t.i, {stage_sql_expr()} AS stage
        FROM unnest(
            CAST(:i AS integer[]),
            CAST(:pub AS text[]),
            CAST(:rec AS text[]),
            CAST(:stl AS text[]),
            CAST(:ship AS text[]),
            CAST(:act AS boolean[]),
            CAST(:sched AS date[])
        ) AS t(i, publish_status, recall_status, settlement_status, ship_status,
               is_active, scheduled_publish_date)
        ORDER BY t.i
        """
    )
    rows = (
        await session.execute(
            sql,
            {
                "i": list(range(len(combos))),
                "pub": list(cols[0]),
                "rec": list(cols[1]),
                "stl": list(cols[2]),
                "ship": list(cols[3]),
                "act": list(cols[4]),
                "sched": list(cols[5]),
                "today": today,
                **TH.sql_params(),
            },
        )
    ).all()
    assert [r[0] for r in rows] == list(range(len(combos)))
    return [r[1] for r in rows]


def _py_stages(
    combos: list[tuple[str, str, str, str | None, bool, date | None]], today: date
) -> list[str]:
    return [
        compute_stage(
            publish_status=pub,
            recall_status=rec,
            settlement_status=stl,
            ship_status=ship,
            is_active=act,
            scheduled_publish_date=sched,
            today=today,
            thresholds=TH,
        )
        for pub, rec, stl, ship, act, sched in combos
    ]


async def test_python_and_sql_agree_on_every_combo(session: AsyncSession) -> None:
    today = get_today()
    combos = _combos(today)
    assert len(combos) == 4800

    py = _py_stages(combos, today)
    sql = await _sql_stages(session, combos, today)

    mismatches = [
        f"{combos[i]!r}: py={p!r} sql={s!r}"
        for i, (p, s) in enumerate(zip(py, sql, strict=True))
        if p != s
    ]
    assert not mismatches, f"{len(mismatches)} 组不一致：\n" + "\n".join(mismatches[:20])

    # 场景有效性：可达阶段全命中、不可达的一个都没出现、没有矩阵外的名字
    hit = set(py)
    reachable = set(PROMOTION_STAGES) - _UNREACHABLE_IN_PR2
    assert len(reachable) == 17
    assert hit == reachable, f"缺 {sorted(reachable - hit)}，多 {sorted(hit - reachable)}"


async def test_list_with_cte_carries_stage(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    """列表 CTE 算出的 ``stage`` 与 Python 版逐行相等（覆盖 JOIN 了款式的那层不歧义）。"""
    today = get_today()
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        pr = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style(style_code="STAGE_CTE")
        blogger = await blogger_factory.blogger()
        cases: list[dict[str, Any]] = [
            {"ship_status": "待发货"},
            {"ship_status": "待打单", "publish_status": "异常"},
            {"scheduled_publish_date": today - timedelta(days=2)},
            {"is_active": False, "ship_status": "待发货"},
            {"publish_status": "已发布", "settlement_status": "待付款"},
            {"publish_status": "已取消", "recall_status": "召回成功"},
            {"publish_status": "已发布", "settlement_status": "未核查"},
        ]
        expected: dict[Any, str] = {}
        for kw in cases:
            # 工厂默认编号只有 3 位随机，一次建 7 张约 0.5% 撞唯一键：给足 8 位
            p = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                internal_code=f"DESG{uuid4().hex[:8].upper()}",
                **kw,
            )
            expected[p.id] = compute_stage(
                publish_status=p.publish_status,
                recall_status=p.recall_status,
                settlement_status=p.settlement_status,
                ship_status=p.ship_status,
                is_active=p.is_active,
                scheduled_publish_date=p.scheduled_publish_date,
                today=today,
                thresholds=TH,
            )

        rows, _total = await PromotionRepository(session).list_with_cte(
            tenant_id=tenant_a.id,
            filters=RepoFilters(is_active=None),  # 默认只列启用的，停用那张也要看
            page=1,
            page_size=100,
            today=today,
            urge_threshold_days=TH.urge_days,
            important_threshold_days=TH.important_days,
        )
        got = {r.promotion.id: r.stage for r in rows if r.promotion.id in expected}
        assert got == expected
        assert set(expected.values()) == {
            "待推送仓库",
            "待仓库发货",
            "超时",
            "已停用",
            "待财务付款",
            "已完结 · 召回",
            "状态异常",
        }
    finally:
        tenant_id_ctx.reset(token)
