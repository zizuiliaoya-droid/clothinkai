"""催发附聊天截图（7a-3）。

截图路径（``urge_once(screenshot=...)`` / ``POST /api/urge/promotions/{id}/urge-with-screenshot``）
上线以来没有测试；7a-3 让推广页也能直接附截图，入口多了先把后端这条路钉住。

守的不变量：
- 合法图片 + 备注 → 记录带截图签名 URL，attachment 行 purpose = urge_screenshot、status = ready
- 内容与声明不符的伪图、不支持的格式 → ``UrgeScreenshotInvalidError``，整次催发回滚：
  不留任务、不留记录、不留附件行（不能「图没存上但次数 +1」）
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import PublishStatus
from app.modules.urge.exceptions import UrgeScreenshotInvalidError
from app.modules.urge.service import UrgeService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_GIF = b"GIF89a" + b"\x00" * 64


class _FakeS3:
    """只替掉要联网的三个调用，Attachment 行与 FK 都走真实路径。"""

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


async def _promo(
    promotion_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    pr: Any,
    *,
    code: str,
) -> Any:
    style = await product_factory.style(style_code=code)
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=pr,
        publish_status=PublishStatus.UNPUBLISHED.value,
    )


async def _leftovers(
    session: AsyncSession, *, tenant_id: UUID, promotion_id: UUID
) -> dict[str, int]:
    """被拒之后库里还剩什么：任务、记录、催发截图附件行。"""
    params = {"t": tenant_id, "p": promotion_id}
    tasks = (
        await session.execute(
            sa_text("SELECT COUNT(*) FROM urge_task WHERE tenant_id = :t AND promotion_id = :p"),
            params,
        )
    ).scalar_one()
    records = (
        await session.execute(
            sa_text("SELECT COUNT(*) FROM urge_record WHERE tenant_id = :t AND promotion_id = :p"),
            params,
        )
    ).scalar_one()
    attachments = (
        await session.execute(
            sa_text(
                "SELECT COUNT(*) FROM attachment "
                "WHERE tenant_id = :t AND purpose = 'urge_screenshot'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    return {"tasks": tasks, "records": records, "attachments": attachments}


class TestUrgeWithScreenshot:
    async def test_valid_png_with_note_is_stored(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UGS_OK"
            )
            detail = await UrgeService(session).urge_once(
                promo.id,
                pr,
                note="微信催了，附聊天截图",
                screenshot=("chat.png", "image/png", _PNG),
            )

            assert detail.urge_count == 1
            assert len(detail.records) == 1
            record = detail.records[0]
            assert record.trigger_type == "手动"
            assert record.note == "微信催了，附聊天截图"
            assert record.screenshot_url is not None

            att = (
                (
                    await session.execute(
                        sa_text(
                            "SELECT a.id, a.purpose, a.status, a.bucket, a.mime_type, "
                            "a.size_bytes, a.r2_key "
                            "FROM urge_record r JOIN attachment a "
                            "ON a.id = r.screenshot_attachment_id "
                            "WHERE r.tenant_id = :t AND r.promotion_id = :p"
                        ),
                        {"t": tenant_a.id, "p": promo.id},
                    )
                )
                .mappings()
                .one()
            )
            assert att["purpose"] == "urge_screenshot"
            assert att["status"] == "ready"
            assert att["bucket"] == "private"
            assert att["mime_type"] == "image/png"
            assert att["size_bytes"] == len(_PNG)
            # 签名 URL 指向的正是这张截图
            assert record.screenshot_url.endswith(att["r2_key"])
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize(
        ("filename", "mime_type", "data", "reason"),
        [
            (
                "fake.png",
                "image/png",
                "这不是图片，只是一段文字".encode(),
                "内容与声明的格式不一致",
            ),
            ("anim.gif", "image/gif", _GIF, "仅支持 JPG、PNG、WebP"),
        ],
        ids=["fake_png", "gif"],
    )
    async def test_invalid_screenshot_rejected_and_rolled_back(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        filename: str,
        mime_type: str,
        data: bytes,
        reason: str,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UGS_BAD"
            )
            # 服务失败时会 rollback：前置数据先提交，id 先取出来（rollback 后 ORM 对象已过期）
            await session.commit()
            tenant_id, promotion_id = tenant_a.id, promo.id

            with pytest.raises(UrgeScreenshotInvalidError) as exc_info:
                await UrgeService(session).urge_once(
                    promotion_id,
                    pr,
                    note="附了张不合格的图",
                    screenshot=(filename, mime_type, data),
                )
            assert reason in str(exc_info.value)

            # 任务是在校验截图之前建的，必须跟着一起回滚
            assert await _leftovers(session, tenant_id=tenant_id, promotion_id=promotion_id) == {
                "tasks": 0,
                "records": 0,
                "attachments": 0,
            }
        finally:
            tenant_id_ctx.reset(token)
