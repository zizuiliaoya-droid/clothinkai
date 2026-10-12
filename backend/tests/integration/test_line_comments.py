"""留言 / 贴图接口 ``POST /api/lines/{line_id}/comments``（流程线设计 7.6、6.3「留言」行；9.3 G 块「留言」）。

守的不变量：
- 文字或一张图至少一项：都空（含只有空白）→ 422 ``LINE_COMMENT_EMPTY``；超 2000 字 → 422；
  伪图（mime 写 png、内容是文本）→ 422 ``LINE_COMMENT_IMAGE_INVALID``（注入 C_IMG：不验魔数）
- 看不到这条线 → 404；看得到但两种 write 都不持（运营、财务）→ 403（注入 C_OPS）
- 挂哪一边：线上已有推广单只挂 ``promotion_id``，还没有只挂 ``negotiation_id``（注入 C_TARGET：总挂 negotiation_id）——
  PR 乙给 PR 甲谈款生成的推广单留言，PR 乙读得到；未定稿谈款的留言只挂谈款
- 贴图：私有桶、用途 ``line_comment_image``、``ready``；事件写失败整单回滚并删掉已传的对象
- 返回新写的那条事件（6.5 的事件形状），之后按 ``line_id`` 读得到
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventRepository
from app.modules.flow.models import LineEvent
from app.modules.flow.timeline import LineEventService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 最小合法 PNG（魔数 + 一点内容），check_image_payload 只验魔数不解码
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _FakeS3:
    """只替掉联网的调用：``create_upload_record`` 在 R2 未配置时直接抛错，没有本地回退。"""

    def __init__(self) -> None:
        self.put: list[str] = []
        self.deleted: list[str] = []

    def put_object(self, **kw: Any) -> dict[str, Any]:
        self.put.append(kw["Key"])
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deleted.append(kw["Key"])
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        key = (kw.get("Params") or {}).get("Key", "")
        return f"https://fake-r2.local/{key}"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@dataclass
class _Line:
    line_id: UUID
    promotion_id: UUID
    negotiation_id: UUID


@pytest.fixture
async def line(
    tenant_ctx: None,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    negotiation_factory: Any,
) -> _Line:
    """PR 甲谈款定稿生成的推广单：谈款与推广单同一条线。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
    neg = await negotiation_factory.negotiation(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        status="已定稿",
        promotion_id=promo.id,
        line_id=promo.line_id,
    )
    return _Line(line_id=promo.line_id, promotion_id=promo.id, negotiation_id=neg.id)


async def _pending_negotiation(
    flow_users: Any, product_factory: Any, blogger_factory: Any, negotiation_factory: Any
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await negotiation_factory.negotiation(
        style=style, blogger=blogger, pr=flow_users.pr, status="主管待审"
    )


async def _post(
    session: AsyncSession,
    user: Any,
    line_id: UUID,
    *,
    text: str | None = None,
    image: tuple[str, bytes, str] | None = None,
) -> Any:
    """真实路由 + 用户的有效权限（照 test_line_timeline._call），multipart 提交。"""
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    data = {"text": text} if text is not None else {}
    files = {"image": image} if image is not None else None
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # 都空时也要按 multipart 发（与前端一致），塞一个无关字段占位
            return await c.post(
                f"/api/lines/{line_id}/comments",
                data=data or {"_": ""},
                files=files,
            )
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def _comments(session: AsyncSession, line_id: UUID) -> list[LineEvent]:
    stmt = (
        select(LineEvent)
        .where(LineEvent.line_id == line_id, LineEvent.event_type == "line.comment")
        .order_by(LineEvent.seq)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _comment_images(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(Attachment)
        .where(Attachment.purpose == "line_comment_image")
    )
    return int((await session.execute(stmt)).scalar_one())


@pytest.mark.usefixtures("tenant_ctx")
class TestCommentWrites:
    async def test_text_comment_on_promotion_line(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        resp = await _post(session, flow_users.pr, line.line_id, text="  博主说周五发  ")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert (body["event_type"], body["group"], body["note"]) == (
            "line.comment",
            "留言",
            "博主说周五发",
        )
        assert body["actor"]["id"] == str(flow_users.pr.id)
        assert body["attachment"] is None and body["payload"] == {}
        rows = await _comments(session, line.line_id)
        assert [(r.promotion_id, r.negotiation_id, r.actor_id) for r in rows] == [
            (line.promotion_id, None, flow_users.pr.id)
        ]
        assert body["seq"] == rows[0].seq
        # 之后按 line_id 读得到
        read = await LineEventService(session).read(line.line_id, flow_users.pr)
        assert [e.note for e in read.events if e.event_type == "line.comment"] == ["博主说周五发"]

    async def test_image_comment(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        resp = await _post(
            session, flow_users.pr_manager, line.line_id, image=("chat.png", _PNG, "image/png")
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["note"] is None
        att = body["attachment"]
        assert att is not None and att["mime"] == "image/png"
        row = await session.get(Attachment, UUID(att["id"]))
        assert row is not None
        assert (row.bucket, row.purpose, row.status, row.size_bytes) == (
            "private",
            "line_comment_image",
            "ready",
            len(_PNG),
        )
        assert s3.put == [row.r2_key]
        assert att["url"] == f"https://fake-r2.local/{row.r2_key}"
        rows = await _comments(session, line.line_id)
        assert [(r.attachment_id, r.promotion_id) for r in rows] == [(row.id, line.promotion_id)]

    async def test_text_and_image_together(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        resp = await _post(
            session,
            flow_users.pr,
            line.line_id,
            text="截图在这",
            image=("chat.png", _PNG, "image/png"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["note"] == "截图在这" and body["attachment"] is not None
        assert len(await _comments(session, line.line_id)) == 1


@pytest.mark.usefixtures("tenant_ctx")
class TestCommentValidation:
    async def test_fake_image_422(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        resp = await _post(
            session,
            flow_users.pr,
            line.line_id,
            text="看图",
            image=("fake.png", b"not an image at all", "image/png"),
        )
        assert (resp.status_code, resp.json()["code"]) == (
            422,
            "LINE_COMMENT_IMAGE_INVALID",
        ), resp.text
        assert "内容与声明的格式不一致" in resp.json()["message"]
        assert await _comments(session, line.line_id) == []
        assert await _comment_images(session) == 0 and s3.put == []

    async def test_wrong_mime_422(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        resp = await _post(
            session, flow_users.pr, line.line_id, image=("a.pdf", b"%PDF-1.4", "application/pdf")
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "LINE_COMMENT_IMAGE_INVALID")

    @pytest.mark.parametrize("text", [None, "", "   \n "])
    async def test_empty_422(
        self,
        session: AsyncSession,
        flow_users: Any,
        line: _Line,
        s3: _FakeS3,
        text: str | None,
    ) -> None:
        resp = await _post(session, flow_users.pr, line.line_id, text=text)
        assert (resp.status_code, resp.json()["code"]) == (422, "LINE_COMMENT_EMPTY"), resp.text
        assert await _comments(session, line.line_id) == []

    async def test_text_length_limit(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        too_long = await _post(session, flow_users.pr, line.line_id, text="字" * 2001)
        assert (too_long.status_code, too_long.json()["code"]) == (422, "VALIDATION_ERROR")
        assert await _comments(session, line.line_id) == []
        ok = await _post(session, flow_users.pr, line.line_id, text="字" * 2000)
        assert ok.status_code == 201, ok.text


@pytest.mark.usefixtures("tenant_ctx")
class TestCommentAccess:
    async def test_operations_and_finance_403(
        self,
        session: AsyncSession,
        flow_users: Any,
        line: _Line,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
        s3: _FakeS3,
    ) -> None:
        """运营 / 财务看得到线（时间线 200），但两种 write 都不持 → 403。"""
        pending = await _pending_negotiation(
            flow_users, product_factory, blogger_factory, negotiation_factory
        )
        svc = LineEventService(session)
        for user in (flow_users.operations, flow_users.finance):
            for line_id in (line.line_id, pending.line_id):
                await svc.read(line_id, user)  # 场景有效：第 1 层过得去
                resp = await _post(session, user, line_id, text="我也说一句")
                assert (resp.status_code, resp.json()["code"]) == (
                    403,
                    "PERMISSION_DENIED",
                ), resp.text
        assert await _comments(session, line.line_id) == []
        assert await _comments(session, pending.line_id) == []

    async def test_outsider_404(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        for line_id in (line.line_id, uuid4()):
            user = flow_users.warehouse if line_id == line.line_id else flow_users.admin
            resp = await _post(session, user, line_id, text="x")
            assert (resp.status_code, resp.json()["code"]) == (404, "LINE_NOT_FOUND"), resp.text

    async def test_pr2_on_pr1_promotion_attaches_promotion_only(
        self, session: AsyncSession, flow_users: Any, line: _Line, s3: _FakeS3
    ) -> None:
        """PR 乙看不到 PR 甲的谈款，但能给生成的推广单留言：只挂 promotion_id，PR 乙读得到。"""
        resp = await _post(session, flow_users.pr2, line.line_id, text="我来跟进发布")
        assert resp.status_code == 201, resp.text
        rows = await _comments(session, line.line_id)
        assert [(r.promotion_id, r.negotiation_id) for r in rows] == [(line.promotion_id, None)]
        read = await LineEventService(session).read(line.line_id, flow_users.pr2)
        assert read.hidden_segments == []
        assert [e.note for e in read.events] == ["我来跟进发布"]

    async def test_pending_negotiation_attaches_negotiation_only(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
        s3: _FakeS3,
    ) -> None:
        """还没推广单：只挂 negotiation_id；PR 乙看不到这张谈款 → 404。"""
        neg = await _pending_negotiation(
            flow_users, product_factory, blogger_factory, negotiation_factory
        )
        for user in (flow_users.pr, flow_users.pr_manager):
            resp = await _post(session, user, neg.line_id, text=f"{user.username} 留言")
            assert resp.status_code == 201, resp.text
        rows = await _comments(session, neg.line_id)
        assert [(r.negotiation_id, r.promotion_id) for r in rows] == [
            (neg.id, None),
            (neg.id, None),
        ]
        outsider = await _post(session, flow_users.pr2, neg.line_id, text="x")
        assert (outsider.status_code, outsider.json()["code"]) == (404, "LINE_NOT_FOUND")

    async def test_draft_only_owner(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        negotiation_factory: Any,
        s3: _FakeS3,
    ) -> None:
        """草稿只有本人看得到、能留言；管理员看不到草稿 → 404。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        draft = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr
        )
        assert (
            await _post(session, flow_users.pr, draft.line_id, text="草稿备忘")
        ).status_code == 201
        resp = await _post(session, flow_users.admin, draft.line_id, text="x")
        assert (resp.status_code, resp.json()["code"]) == (404, "LINE_NOT_FOUND")


@pytest.mark.usefixtures("tenant_ctx")
class TestCommentRollback:
    async def test_event_failure_rolls_back_and_deletes_object(
        self,
        session: AsyncSession,
        flow_users: Any,
        line: _Line,
        s3: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """事件写失败：附件行跟着回滚，已传的对象删掉（不留孤儿），错误照常抛出。"""
        await session.commit()  # 线上的单据先落下（测试库 savepoint 模式），rollback 只回本次留言

        async def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("append failed")

        monkeypatch.setattr(LineEventRepository, "append", _boom)
        with pytest.raises(RuntimeError, match="append failed"):
            await LineEventService(session).comment(
                line.line_id, flow_users.pr, text=None, image=("chat.png", "image/png", _PNG)
            )
        assert len(s3.put) == 1 and s3.deleted == s3.put
        assert await _comment_images(session) == 0
        assert await _comments(session, line.line_id) == []
