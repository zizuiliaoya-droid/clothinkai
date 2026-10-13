"""催发「标记完成」与重开（流程线 PR-5 S2；设计 3.9 U1 ~ U3、6.3、7.6、9.3 C 块）。

守的不变量：
- U1 只在进行中：截图必传，没图 → 422 ``URGE_COMPLETE_SCREENSHOT_REQUIRED`` 且库不变（service 先报友好的错，
  库级 CHECK ``ck_urge_task_ck_urge_task_complete_screenshot`` 兜底，注入 C1 URGE_NO_SHOT 证明它在）；
  伪图 422；已关闭（任何原因）再完成 → 409。完成写 ``close_attachment_id`` 与 ``urge.completed``（附件 = 截图），
  不写 urge_record（看板「本周已催发」数的是它）
- U2 已完成的被手动催 → 同事务重开：三件套与截图外键清空、计次 +1，事件顺序 reopened → sent，
  旧截图仍挂在 ``urge.completed`` 上（注入 URGE_NO_REOPEN）；博主已发布 / 已取消 / 手动关闭的照旧 409
- U3 批量催发跳过已完成的（计进 ``skipped_closed``）、每日扫描不重开不计次（注入 URGE_BUMP_ANY）
- 接口：PR 200；运营 403 ``PERMISSION_DENIED``
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.exceptions import (
    UrgeCompleteScreenshotRequiredError,
    UrgeScreenshotInvalidError,
    UrgeTaskClosedError,
)
from app.modules.urge.service import UrgeService
from tests.flow_event_helpers import assert_payload_registered, events_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_T = LineEventType
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_SHOT = ("done.png", "image/png", _PNG)


class _FakeS3:
    """只替掉联网的调用（照 test_urge_screenshot）。"""

    def put_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def delete_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture(autouse=True)
def _fake_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _open_task(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    **kw: Any,
) -> tuple[UUID, UUID, UUID]:
    """建一张未发布的推广单并手动催一次（任务进行中、已提交），返回 ``(task_id, promotion_id, line_id)``。"""
    style = kw.pop("style", None) or await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style, blogger=blogger, pr=flow_users.pr, publish_status="未发布", **kw
    )
    pid, line_id = promo.id, promo.line_id
    task = await UrgeService(session).urge_once(pid, flow_users.pr, note="第一次催")
    return task.id, pid, line_id


async def _task_row(session: AsyncSession, task_id: UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                sa_text(
                    "SELECT status, close_reason, closed_at, close_attachment_id, urge_count "
                    "FROM urge_task WHERE id = :t"
                ),
                {"t": task_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def _count(session: AsyncSession, sql: str, **params: Any) -> int:
    return int((await session.execute(sa_text(sql), params)).scalar_one())


async def _complete_shots(session: AsyncSession, tenant_id: UUID) -> int:
    return await _count(
        session,
        "SELECT COUNT(*) FROM attachment WHERE tenant_id = :t "
        "AND purpose = 'urge_complete_screenshot'",
        t=tenant_id,
    )


async def _call(
    session: AsyncSession,
    user: Any,
    path: str,
    *,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> Any:
    """真实路由（含 multipart 解析）+ 用户的有效权限（照 test_negotiation_api）。"""
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.post(path, data=data, files=files)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.mark.usefixtures("tenant_ctx")
class TestComplete:
    async def test_without_screenshot_422_and_unchanged(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """C1：没图 → 友好的 422，库不变。去掉 service 校验（URGE_NO_SHOT）→ CHECK 拦下，拿不到 422 而红。"""
        task_id, _pid, line_id = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        tenant_id = flow_users.tenant.id

        with pytest.raises(UrgeCompleteScreenshotRequiredError):
            await UrgeService(session).complete_task(task_id, flow_users.pr, note="发了")

        row = await _task_row(session, task_id)
        assert (row["status"], row["close_reason"], row["closed_at"]) == ("进行中", None, None)
        assert row["close_attachment_id"] is None
        assert await events_of(session, line_id=line_id, event_type=_T.URGE_COMPLETED) == []
        assert await _complete_shots(session, tenant_id) == 0

    async def test_with_screenshot_closes_as_completed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        task_id, pid, line_id = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        records_before = await _count(
            session, "SELECT COUNT(*) FROM urge_record WHERE urge_task_id = :t", t=task_id
        )

        detail = await UrgeService(session).complete_task(
            task_id, flow_users.pr, note="博主说已经发了", screenshot=_SHOT
        )

        assert (detail.status, detail.close_reason) == ("已关闭", "已完成")
        assert detail.closed_at is not None and detail.over_limit is False
        row = await _task_row(session, task_id)
        att = (
            (
                await session.execute(
                    sa_text("SELECT purpose, status, bucket, r2_key FROM attachment WHERE id = :a"),
                    {"a": row["close_attachment_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert (att["purpose"], att["status"], att["bucket"]) == (
            "urge_complete_screenshot",
            "ready",
            "private",
        )
        assert detail.close_screenshot_url is not None
        assert detail.close_screenshot_url.endswith(att["r2_key"])
        # 完成不写 urge_record（看板「本周已催发」数的是 urge_record）
        assert (
            await _count(
                session, "SELECT COUNT(*) FROM urge_record WHERE urge_task_id = :t", t=task_id
            )
            == records_before
        )

        (completed,) = await events_of(session, line_id=line_id, event_type=_T.URGE_COMPLETED)
        assert completed.actor_id == flow_users.pr.id and completed.promotion_id == pid
        assert completed.note == "博主说已经发了"
        assert completed.attachment_id == row["close_attachment_id"]
        assert completed.payload == {"urge_task_id": str(task_id), "close_reason": "已完成"}
        assert_payload_registered(completed.payload)

    async def test_fake_image_422_and_rolled_back(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        task_id, _pid, line_id = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        tenant_id = flow_users.tenant.id

        with pytest.raises(UrgeScreenshotInvalidError):
            await UrgeService(session).complete_task(
                task_id, flow_users.pr, screenshot=("fake.png", "image/png", "不是图".encode())
            )

        row = await _task_row(session, task_id)
        assert (row["status"], row["close_attachment_id"]) == ("进行中", None)
        assert await events_of(session, line_id=line_id, event_type=_T.URGE_COMPLETED) == []
        assert await _complete_shots(session, tenant_id) == 0

    async def test_closed_task_cannot_complete(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """手动关闭的、已完成的都 409；先判状态再判截图（没图也是 409 不是 422）。"""
        svc = UrgeService(session)
        closed_id, _p1, _l1 = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        await svc.close_task(closed_id, flow_users.pr_manager, reason="改走召回")
        done_id, _p2, _l2 = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        await svc.complete_task(done_id, flow_users.pr, screenshot=_SHOT)
        tenant_id = flow_users.tenant.id

        for task_id in (closed_id, done_id):
            with pytest.raises(UrgeTaskClosedError):
                await UrgeService(session).complete_task(task_id, flow_users.pr, screenshot=_SHOT)
            with pytest.raises(UrgeTaskClosedError):
                await UrgeService(session).complete_task(task_id, flow_users.pr)
        assert await _complete_shots(session, tenant_id) == 1
        assert (await _task_row(session, closed_id))["close_reason"] == "手动关闭"


@pytest.mark.usefixtures("tenant_ctx")
class TestReopen:
    async def test_manual_urge_reopens_completed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """U2：已完成的被手动催 → 重开、计次、三件套与外键清空；旧截图仍在事件里；reopened → sent。"""
        task_id, pid, line_id = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        svc = UrgeService(session)
        await svc.complete_task(task_id, flow_users.pr, screenshot=_SHOT)
        old_shot = (await _task_row(session, task_id))["close_attachment_id"]
        before = len(await events_of(session, line_id=line_id))

        detail = await svc.urge_once(pid, flow_users.pr2, note="博主删帖了，再催")

        assert (detail.status, detail.urge_count) == ("进行中", 2)
        assert (detail.closed_at, detail.close_reason, detail.close_screenshot_url) == (
            None,
            None,
            None,
        )
        row = await _task_row(session, task_id)
        assert (row["status"], row["close_reason"], row["closed_at"]) == ("进行中", None, None)
        assert row["close_attachment_id"] is None
        # 截图本身还在附件表，且仍挂在 urge.completed 上
        assert await _count(session, "SELECT COUNT(*) FROM attachment WHERE id = :a", a=old_shot)
        (completed,) = await events_of(session, line_id=line_id, event_type=_T.URGE_COMPLETED)
        assert completed.attachment_id == old_shot

        new = (await events_of(session, line_id=line_id))[before:]
        assert [e.event_type for e in new] == [_T.URGE_REOPENED.value, _T.URGE_SENT.value]
        reopened, sent = new
        assert reopened.actor_id == flow_users.pr2.id and reopened.promotion_id == pid
        assert reopened.payload == {"urge_task_id": str(task_id), "trigger": "手动"}
        assert sent.payload["urge_count"] == 2 and sent.note == "博主删帖了，再催"
        for e in new:
            assert_payload_registered(e.payload)

    @pytest.mark.parametrize("reason", ["手动关闭", "博主已发布", "已取消"])
    async def test_other_close_reasons_still_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        reason: str,
    ) -> None:
        task_id, pid, line_id = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        # 推广单仍是未发布（可催），只把任务按该原因关掉：判的是关闭原因，不是推广单状态
        await session.execute(
            sa_text(
                "UPDATE urge_task SET status = '已关闭', close_reason = :r, closed_at = now() "
                "WHERE id = :t"
            ),
            {"r": reason, "t": task_id},
        )
        await session.commit()
        before = len(await events_of(session, line_id=line_id))

        with pytest.raises(UrgeTaskClosedError):
            await UrgeService(session).urge_once(pid, flow_users.pr, note="还想催")

        row = await _task_row(session, task_id)
        assert (row["status"], row["close_reason"], row["urge_count"]) == ("已关闭", reason, 1)
        assert len(await events_of(session, line_id=line_id)) == before

    async def test_batch_skips_completed(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """U3：批量催发不重开，已完成的计进 ``skipped_closed``、不计次。"""
        style = await product_factory.style()
        style_id = style.id
        done_id, _p1, done_line = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory, style=style
        )
        open_id, _p2, _l2 = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory, style=style
        )
        svc = UrgeService(session)
        await svc.complete_task(done_id, flow_users.pr, screenshot=_SHOT)
        before = len(await events_of(session, line_id=done_line))

        resp = await svc.urge_batch(style_id, flow_users.pr, note="批量催")

        assert (resp.urged_count, resp.skipped_closed, resp.task_ids) == (1, 1, [open_id])
        row = await _task_row(session, done_id)
        assert (row["status"], row["close_reason"], row["urge_count"]) == ("已关闭", "已完成", 1)
        assert row["close_attachment_id"] is not None
        assert len(await events_of(session, line_id=done_line)) == before

    async def test_daily_scan_does_not_reopen(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """U3：排期在自动催发窗口里，已完成的任务扫描时不重开、不计次、不留记录。"""
        today = get_today()
        task_id, _pid, _line = await _open_task(
            session,
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            scheduled_publish_date=today + timedelta(days=1),
        )
        svc = UrgeService(session)
        await svc.complete_task(task_id, flow_users.pr, screenshot=_SHOT)
        records_before = await _count(
            session, "SELECT COUNT(*) FROM urge_record WHERE urge_task_id = :t", t=task_id
        )

        result = await svc.scan_tenant(tenant_id=flow_users.tenant.id, today=today)

        assert (result.tasks_created, result.records_created, result.tasks_closed) == (0, 0, 0)
        assert result.skipped_same_day == 1
        row = await _task_row(session, task_id)
        assert (row["status"], row["close_reason"], row["urge_count"]) == ("已关闭", "已完成", 1)
        assert (
            await _count(
                session, "SELECT COUNT(*) FROM urge_record WHERE urge_task_id = :t", t=task_id
            )
            == records_before
        )


@pytest.mark.usefixtures("tenant_ctx")
class TestCompleteRoute:
    async def test_route_scope_and_validation(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        task_id, _pid, _line = await _open_task(
            session, flow_users, product_factory, blogger_factory, promotion_factory
        )
        path = f"/api/urge/tasks/{task_id}/complete"
        files = {"file": ("done.png", _PNG, "image/png")}

        resp = await _call(session, flow_users.operations, path, files=files)
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
        resp = await _call(session, flow_users.pr, path, data={"note": "发了"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "URGE_COMPLETE_SCREENSHOT_REQUIRED"
        resp = await _call(session, flow_users.pr, path, data={"note": "长" * 2001}, files=files)
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR")

        resp = await _call(session, flow_users.pr, path, data={"note": "发了"}, files=files)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["status"], body["close_reason"]) == ("已关闭", "已完成")
        assert body["close_screenshot_url"].startswith("https://fake-r2.local/")

        resp = await _call(session, flow_users.pr, path, files=files)
        assert (resp.status_code, resp.json()["code"]) == (409, "URGE_TASK_CLOSED")
        resp = await _call(session, flow_users.pr, f"/api/urge/tasks/{UUID(int=1)}/complete")
        assert (resp.status_code, resp.json()["code"]) == (404, "URGE_TASK_NOT_FOUND")
