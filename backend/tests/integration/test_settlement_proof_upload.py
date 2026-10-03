"""付款截图后端代传：一个请求完成「上传 → 已付款」。

原来是浏览器直传 R2，生产私有桶没配 CORS，预检被拒成 403，前端只报 ``Failed to fetch``
—— 付款截图一张都传不上去（生产留下 18 个卡在 uploading 的附件）。

守的不变量：
- 成功路径与原来的 mark_paid 完全一致：FB4 附件校验、状态推进、SettlementPaid 事件
- **上传之前**就挡掉：没有付款权限、结算单状态不对、付款日期在未来、图片格式 / 内容不对
  —— 这些情况桶里不能多出一个文件
- 上传之后任何一步失败：结算单不动、附件行不留、已上传的对象被删掉
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import AttachmentError
from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.exceptions import (
    FieldPermissionDenied,
    InvalidPaymentProofImageError,
    PaymentFieldMissingError,
)
from app.modules.finance.service import SettlementService
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 最小合法 PNG（魔数 + 一点内容），check_image_payload 只验魔数不解码
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _RecordingS3:
    """记录上传 / 删除调用的假 R2 client，Attachment 行与状态机都走真实路径。"""

    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []
        self.deletes: list[str] = []
        self.fail_put = False

    def put_object(self, **kw: Any) -> dict[str, Any]:
        if self.fail_put:
            raise ConnectionError("R2 unreachable")
        self.puts.append(kw)
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture
def r2(monkeypatch: pytest.MonkeyPatch) -> _RecordingS3:
    from app.core import attachment as att_mod

    fake = _RecordingS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


async def _pending_finance(
    settlement_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    *,
    status: str = "待财务付款",
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await settlement_factory.settlement(
        style=style,
        blogger=blogger,
        settlement_status=status,
        payment_amount=Decimal("480.00"),
    )


async def _state(session: AsyncSession, settlement_id: Any) -> tuple[str, Any]:
    row = (
        await session.execute(
            sa_text(
                "SELECT settlement_status, payment_proof_attachment_id "
                "FROM settlement WHERE id = :id"
            ),
            {"id": settlement_id},
        )
    ).one()
    return row.settlement_status, row.payment_proof_attachment_id


async def _proof_rows(session: AsyncSession, user_id: Any) -> int:
    return int(
        (
            await session.execute(
                sa_text(
                    "SELECT count(*) FROM attachment "
                    "WHERE purpose = 'settlement_proof' AND created_by = :u"
                ),
                {"u": user_id},
            )
        ).scalar_one()
    )


class TestProofUploadHappyPath:
    async def test_upload_marks_paid_and_stores_object(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        event_capture: list[Any],
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)

            resp = await SettlementService(session).upload_payment_proof_file(
                s.id,
                payment_date=get_today(),
                filename="proof.png",
                mime_type="image/png",
                data=_PNG,
                user=finance,
            )

            assert resp.settlement_status == "已付款"
            assert resp.payment_proof_attachment_id is not None
            assert resp.payment_proof_signed_url is not None
            att = (
                await session.execute(
                    sa_text(
                        "SELECT bucket, purpose, status, mime_type, size_bytes, r2_key "
                        "FROM attachment WHERE id = :id"
                    ),
                    {"id": resp.payment_proof_attachment_id},
                )
            ).one()
            assert (att.bucket, att.purpose, att.status, att.mime_type, att.size_bytes) == (
                "private",
                "settlement_proof",
                "ready",
                "image/png",
                len(_PNG),
            )
            # 对象真的传了，键与附件行一致；没有触发补偿删除
            assert [(p["Key"], p["ContentType"]) for p in r2.puts] == [(att.r2_key, "image/png")]
            assert r2.deletes == []
            # 走的是原来的 mark_paid：反向事件照发
            paid = [e for e in event_capture if e.event_type == "SettlementPaid"]
            assert len(paid) == 1 and paid[0].settlement_id == s.id
        finally:
            tenant_id_ctx.reset(token)


class TestRejectedBeforeUpload:
    """这些情况都不能往桶里放文件，也不能留附件行。"""

    async def test_wrong_state_uploads_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(
                settlement_factory, product_factory, blogger_factory, status="待核查"
            )
            with pytest.raises(IllegalStateTransitionError):
                await SettlementService(session).upload_payment_proof_file(
                    s.id,
                    payment_date=get_today(),
                    filename="proof.png",
                    mime_type="image/png",
                    data=_PNG,
                    user=finance,
                )
            assert r2.puts == []
            assert await _proof_rows(session, finance.id) == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_without_pay_permission_uploads_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)
            with pytest.raises(FieldPermissionDenied):
                await SettlementService(session).upload_payment_proof_file(
                    s.id,
                    payment_date=get_today(),
                    filename="proof.png",
                    mime_type="image/png",
                    data=_PNG,
                    user=pr,
                )
            assert r2.puts == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_future_payment_date_uploads_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)
            with pytest.raises(PaymentFieldMissingError):
                await SettlementService(session).upload_payment_proof_file(
                    s.id,
                    payment_date=get_today() + timedelta(days=1),
                    filename="proof.png",
                    mime_type="image/png",
                    data=_PNG,
                    user=finance,
                )
            assert r2.puts == []
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize(
        ("mime_type", "data"),
        [
            ("image/png", b"definitely not a png"),  # 声明 PNG，内容不是
            ("application/pdf", b"%PDF-1.7 ..."),  # 格式不在白名单
            (None, _PNG),  # 浏览器没给类型
            ("image/png", b""),  # 空文件
            ("image/png", _PNG + b"\x00" * (10 * 1024 * 1024)),  # 超过 10MB
        ],
    )
    async def test_bad_image_uploads_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
        mime_type: str | None,
        data: bytes,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)
            with pytest.raises(InvalidPaymentProofImageError):
                await SettlementService(session).upload_payment_proof_file(
                    s.id,
                    payment_date=get_today(),
                    filename="proof.png",
                    mime_type=mime_type,
                    data=data,
                    user=finance,
                )
            assert r2.puts == []
            assert await _proof_rows(session, finance.id) == 0
        finally:
            tenant_id_ctx.reset(token)


class TestFailureAfterUploadStarts:
    """上传开始之后失败：结算单不动、附件行不留、已上传的对象删掉。

    先 commit 一次把造好的数据落进外层事务 —— 测试 session 是 savepoint 模式，不先提交的话
    service 里的 rollback 会连造的结算单一起回滚掉，后面就核对不了「结算单没动」。
    """

    async def test_r2_failure_leaves_settlement_untouched(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)
            await session.commit()
            # service 的 rollback 会让 session 里所有实例过期，之后再读 .id 是隐式加载
            # （async 下 MissingGreenlet），先把要用的值取出来
            settlement_id, finance_id = s.id, finance.id
            r2.fail_put = True

            with pytest.raises(AttachmentError):
                await SettlementService(session).upload_payment_proof_file(
                    settlement_id,
                    payment_date=get_today(),
                    filename="proof.png",
                    mime_type="image/png",
                    data=_PNG,
                    user=finance,
                )
            assert await _state(session, settlement_id) == ("待财务付款", None)
            assert await _proof_rows(session, finance_id) == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_failure_after_upload_deletes_object(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """对象已经进桶、标记已付款这一步失败：对象必须删掉，否则桶里留下没人引用的截图。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _pending_finance(settlement_factory, product_factory, blogger_factory)
            await session.commit()
            settlement_id, finance_id = s.id, finance.id

            async def _boom(*_a: Any, **_k: Any) -> Any:
                raise RuntimeError("mark_paid 挂了")

            monkeypatch.setattr(SettlementService, "_mark_paid", _boom)
            with pytest.raises(RuntimeError):
                await SettlementService(session).upload_payment_proof_file(
                    settlement_id,
                    payment_date=get_today(),
                    filename="proof.png",
                    mime_type="image/png",
                    data=_PNG,
                    user=finance,
                )
            assert len(r2.puts) == 1
            assert r2.deletes == [r2.puts[0]["Key"]]
            assert await _state(session, settlement_id) == ("待财务付款", None)
            assert await _proof_rows(session, finance_id) == 0
        finally:
            tenant_id_ctx.reset(token)
