"""``purge_promotions``：提交式测试清理推广单时，先按外键删干净子表（流程线设计 9.2）。

推广单的子表外键都是 RESTRICT（结款单、金额改动、催发任务与记录、复盘、``promotion_item``、``line_event``），
直接 ``DELETE FROM promotion`` 会报外键错、提交过的数据留在库里；指向推广单的谈款是 SET NULL，
已定稿的谈款被置空会撞「已定稿 ⇔ 有推广单」CHECK，谈款自己的 ``negotiation_item``、``line_event`` 也是 RESTRICT。
这里每张子表各造一行，清理后全为 0，
另一张推广单的子表原样保留。外键 RESTRICT 立即检查，用回滚式 ``session`` 就能测出漏删的一步。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import purge_negotiations, purge_promotions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# (表, 按推广单数行的 SQL)
_CHILD_COUNTS: list[tuple[str, str]] = [
    ("promotion_item", "SELECT COUNT(*) FROM promotion_item WHERE promotion_id = :p"),
    ("settlement", "SELECT COUNT(*) FROM settlement WHERE promotion_id = :p"),
    (
        "settlement_extra_item",
        "SELECT COUNT(*) FROM settlement_extra_item WHERE settlement_id IN "
        "(SELECT id FROM settlement WHERE promotion_id = :p)",
    ),
    ("promotion_amount_log", "SELECT COUNT(*) FROM promotion_amount_log WHERE promotion_id = :p"),
    ("urge_task", "SELECT COUNT(*) FROM urge_task WHERE promotion_id = :p"),
    ("urge_record", "SELECT COUNT(*) FROM urge_record WHERE promotion_id = :p"),
    (
        "blogger_retrospective",
        "SELECT COUNT(*) FROM blogger_retrospective WHERE promotion_id = :p",
    ),
    ("negotiation", "SELECT COUNT(*) FROM negotiation WHERE promotion_id = :p"),
    (
        "negotiation_item",
        "SELECT COUNT(*) FROM negotiation_item WHERE negotiation_id IN "
        "(SELECT id FROM negotiation WHERE promotion_id = :p)",
    ),
    ("line_event(promotion)", "SELECT COUNT(*) FROM line_event WHERE promotion_id = :p"),
    (
        "line_event(negotiation)",
        "SELECT COUNT(*) FROM line_event WHERE negotiation_id IN "
        "(SELECT id FROM negotiation WHERE promotion_id = :p)",
    ),
    ("promotion", "SELECT COUNT(*) FROM promotion WHERE id = :p"),
]


async def _counts(session: AsyncSession, promotion_id: UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for table, sql in _CHILD_COUNTS:
        out[table] = (await session.execute(text(sql), {"p": promotion_id})).scalar_one()
    return out


async def _with_children(
    session: AsyncSession,
    *,
    tenant: Any,
    style: Any,
    sku: Any,
    blogger: Any,
    pr: Any,
    promotion_factory: Any,
    settlement_factory: Any,
) -> UUID:
    """建一张推广单，每张子表各挂一行。"""
    p = await promotion_factory.promotion(
        style=style, blogger=blogger, pr=pr, ship_status="待打单", items=[(style, sku)]
    )
    s = await settlement_factory.settlement(style=style, blogger=blogger, promotion=p, pr=pr)
    params = {
        "t": tenant.id,
        "p": p.id,
        "b": blogger.id,
        "st": style.id,
        "sk": sku.id,
        "pr": pr.id,
        "s": s.id,
        "n": uuid4(),
        "line": p.line_id,
    }
    task_id = uuid4()
    for sql, extra in [
        (
            "INSERT INTO settlement_extra_item (id, tenant_id, settlement_id, item_type, amount) "
            "VALUES (gen_random_uuid(), :t, :s, '运费', 10)",
            {},
        ),
        (
            "INSERT INTO promotion_amount_log (id, tenant_id, promotion_id, field_name, "
            "change_source, before_value, after_value) "
            "VALUES (gen_random_uuid(), :t, :p, 'quote_amount', '手动编辑', 100, 200)",
            {},
        ),
        (
            "INSERT INTO urge_task (id, tenant_id, promotion_id, blogger_id) "
            "VALUES (:task, :t, :p, :b)",
            {"task": task_id},
        ),
        (
            "INSERT INTO urge_record (id, tenant_id, urge_task_id, promotion_id, trigger_type) "
            "VALUES (gen_random_uuid(), :t, :task, :p, '手动')",
            {"task": task_id},
        ),
        (
            "INSERT INTO blogger_retrospective (id, tenant_id, blogger_id, promotion_id, content) "
            "VALUES (gen_random_uuid(), :t, :b, :p, '复盘')",
            {},
        ),
        (
            "INSERT INTO negotiation (id, tenant_id, blogger_id, style_id, pr_id, "
            "cooperation_mode, status, promotion_id, line_id) "
            "VALUES (:n, :t, :b, :st, :pr, '寄拍', '已定稿', :p, :line)",
            {},
        ),
        (
            "INSERT INTO negotiation_item (id, tenant_id, negotiation_id, style_id, sku_id) "
            "VALUES (gen_random_uuid(), :t, :n, :st, :sk)",
            {},
        ),
        # 定稿建单的两类事件：谈款上的（只挂 negotiation_id）与 promotion.created（只挂 promotion_id）
        (
            "INSERT INTO line_event (id, tenant_id, line_id, negotiation_id, event_type, actor_id) "
            "VALUES (gen_random_uuid(), :t, :line, :n, 'negotiation.finalized', :pr)",
            {},
        ),
        (
            "INSERT INTO line_event (id, tenant_id, line_id, promotion_id, event_type, actor_id) "
            "VALUES (gen_random_uuid(), :t, :line, :p, 'promotion.created', :pr)",
            {},
        ),
    ]:
        await session.execute(text(sql), {**params, **extra})
    return p.id


async def test_purge_removes_children_and_keeps_others(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
) -> None:
    style = await product_factory.style()
    sku = await product_factory.sku(style)
    blogger = await blogger_factory.blogger()
    pr = await factory.user(tenant_a)
    deps = {
        "tenant": tenant_a,
        "style": style,
        "sku": sku,
        "blogger": blogger,
        "pr": pr,
        "promotion_factory": promotion_factory,
        "settlement_factory": settlement_factory,
    }
    target = await _with_children(session, **deps)
    other = await _with_children(session, **deps)
    ones = {table: 1 for table, _ in _CHILD_COUNTS}
    # 场景有效：两张单每张子表都真有一行，清理前不是 0
    assert await _counts(session, target) == ones
    assert await _counts(session, other) == ones

    await purge_promotions(session, [target])

    assert await _counts(session, target) == {table: 0 for table, _ in _CHILD_COUNTS}
    assert await _counts(session, other) == ones


async def test_purge_empty_ids_is_noop(session: AsyncSession) -> None:
    await purge_promotions(session, [])
    await purge_negotiations(session, [])


async def _negotiation_counts(session: AsyncSession, negotiation_id: UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for table, sql in [
        ("negotiation", "SELECT COUNT(*) FROM negotiation WHERE id = :n"),
        ("negotiation_item", "SELECT COUNT(*) FROM negotiation_item WHERE negotiation_id = :n"),
        ("line_event", "SELECT COUNT(*) FROM line_event WHERE negotiation_id = :n"),
    ]:
        out[table] = (await session.execute(text(sql), {"n": negotiation_id})).scalar_one()
    return out


async def _event(session: AsyncSession, n: Any, event_type: str) -> None:
    await session.execute(
        text(
            "INSERT INTO line_event (id, tenant_id, line_id, negotiation_id, event_type) "
            "VALUES (gen_random_uuid(), :t, :line, :n, :e)"
        ),
        {"t": n.tenant_id, "line": n.line_id, "n": n.id, "e": event_type},
    )


async def test_purge_negotiations(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
    negotiation_factory: Any,
) -> None:
    """``purge_negotiations``：没定稿的谈款连事件、明细一起删；定稿的连推广单及其子表一起删；
    别的谈款原样保留，从被删谈款复制来的只把 ``copied_from_id`` 置空。"""
    style = await product_factory.style()
    sku = await product_factory.sku(style)
    blogger = await blogger_factory.blogger()
    pr = await factory.user(tenant_a)
    finalized_promotion = await _with_children(
        session,
        tenant=tenant_a,
        style=style,
        sku=sku,
        blogger=blogger,
        pr=pr,
        promotion_factory=promotion_factory,
        settlement_factory=settlement_factory,
    )
    finalized_id = (
        await session.execute(
            text("SELECT id FROM negotiation WHERE promotion_id = :p"), {"p": finalized_promotion}
        )
    ).scalar_one()
    rejected = await negotiation_factory.negotiation(
        style=style, blogger=blogger, pr=pr, status="审核驳回", items=[(style, sku)]
    )
    await _event(session, rejected, "negotiation.created")
    kept = await negotiation_factory.negotiation(
        style=style, blogger=blogger, pr=pr, copied_from_id=rejected.id, items=[(style, None)]
    )
    await _event(session, kept, "negotiation.copied")
    rejected_id, kept_id = rejected.id, kept.id
    ones = {"negotiation": 1, "negotiation_item": 1, "line_event": 1}
    # 场景有效：三张谈款各有明细与事件，定稿那张的推广单子表都在
    assert await _negotiation_counts(session, rejected_id) == ones
    assert await _negotiation_counts(session, kept_id) == ones
    assert await _counts(session, finalized_promotion) == {t: 1 for t, _ in _CHILD_COUNTS}

    await purge_negotiations(session, [finalized_id, rejected_id])

    zeros = {"negotiation": 0, "negotiation_item": 0, "line_event": 0}
    assert await _negotiation_counts(session, rejected_id) == zeros
    assert await _negotiation_counts(session, finalized_id) == zeros
    assert await _counts(session, finalized_promotion) == {t: 0 for t, _ in _CHILD_COUNTS}
    assert await _negotiation_counts(session, kept_id) == ones
    copied_from = (
        await session.execute(
            text("SELECT copied_from_id FROM negotiation WHERE id = :n"), {"n": kept_id}
        )
    ).scalar_one()
    assert copied_from is None
