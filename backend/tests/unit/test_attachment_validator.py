"""U05 单元测试：ProofAttachmentValidator 6 项强校验 + 跨租户 4 层防御（FB4）。

使用 mock AttachmentService.get_by_id 注入各类 attachment 状态，
不依赖真实 DB（跨租户 audit 走 bypass session 部分通过 patch 隔离）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.finance.attachment_validator import (
    MAX_SIZE_BYTES,
    ProofAttachmentValidator,
)
from app.modules.finance.exceptions import (
    AttachmentNotReadyError,
    AttachmentTooLargeError,
    InvalidAttachmentBucketError,
    InvalidAttachmentMimeError,
    InvalidAttachmentPurposeError,
    InvalidAttachmentReferenceError,
)


def _make_attachment(tenant_id, **overrides):
    base = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "bucket": "private",
        "r2_key": f"{tenant_id}/settlement_proof/x/proof.jpg",
        "purpose": "settlement_proof",
        "filename": "proof.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 2048,
        "status": "ready",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_validator(returned_attachment):
    service = MagicMock()
    service.get_by_id = AsyncMock(return_value=returned_attachment)
    return ProofAttachmentValidator(service), service


@pytest.mark.unit
@pytest.mark.asyncio
class TestValidatorHappyPath:
    async def test_valid_attachment_passes(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid)
        validator, _ = _make_validator(att)
        result = await validator.validate(
            session=MagicMock(), attachment_id=att.id, tenant_id=tid
        )
        assert result is att


@pytest.mark.unit
@pytest.mark.asyncio
class TestValidator6Checks:
    """6 项校验各 1 个失败用例。"""

    async def test_not_found(self) -> None:
        validator, _ = _make_validator(None)
        with pytest.raises(InvalidAttachmentReferenceError):
            await validator.validate(
                session=MagicMock(), attachment_id=uuid4(), tenant_id=uuid4()
            )

    async def test_bucket_invalid(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid, bucket="public")
        validator, _ = _make_validator(att)
        with pytest.raises(InvalidAttachmentBucketError):
            await validator.validate(
                session=MagicMock(), attachment_id=att.id, tenant_id=tid
            )

    async def test_purpose_invalid(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid, purpose="avatar")
        validator, _ = _make_validator(att)
        with pytest.raises(InvalidAttachmentPurposeError):
            await validator.validate(
                session=MagicMock(), attachment_id=att.id, tenant_id=tid
            )

    async def test_mime_invalid(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid, mime_type="text/html")
        validator, _ = _make_validator(att)
        with pytest.raises(InvalidAttachmentMimeError):
            await validator.validate(
                session=MagicMock(), attachment_id=att.id, tenant_id=tid
            )

    async def test_size_too_large(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid, size_bytes=MAX_SIZE_BYTES + 1)
        validator, _ = _make_validator(att)
        with pytest.raises(AttachmentTooLargeError):
            await validator.validate(
                session=MagicMock(), attachment_id=att.id, tenant_id=tid
            )

    async def test_status_not_ready(self) -> None:
        tid = uuid4()
        att = _make_attachment(tid, status="uploading")
        validator, _ = _make_validator(att)
        with pytest.raises(AttachmentNotReadyError):
            await validator.validate(
                session=MagicMock(), attachment_id=att.id, tenant_id=tid
            )


@pytest.mark.unit
@pytest.mark.asyncio
class TestCrossTenant4LayerDefense:
    """FB4：跨租户尝试 → 指标 + Sentry + 独立 audit + 422。"""

    async def test_cross_tenant_raises_reference_error(self) -> None:
        owner_tid = uuid4()
        attacker_tid = uuid4()
        att = _make_attachment(owner_tid)
        validator, _ = _make_validator(att)

        with patch.object(
            validator, "_handle_cross_tenant_attempt", new=AsyncMock()
        ) as mock_handler:
            with pytest.raises(InvalidAttachmentReferenceError):
                await validator.validate(
                    session=MagicMock(),
                    attachment_id=att.id,
                    tenant_id=attacker_tid,
                )
            mock_handler.assert_awaited_once()

    async def test_cross_tenant_does_not_leak_existence(self) -> None:
        """跨租户与 not_found 抛同一异常类型（防侧信道）。"""
        owner_tid = uuid4()
        att = _make_attachment(owner_tid)
        validator, _ = _make_validator(att)
        with patch.object(
            validator, "_handle_cross_tenant_attempt", new=AsyncMock()
        ):
            with pytest.raises(InvalidAttachmentReferenceError):
                await validator.validate(
                    session=MagicMock(),
                    attachment_id=att.id,
                    tenant_id=uuid4(),
                )

    async def test_cross_tenant_records_metric(self) -> None:
        """跨租户必须记 tenant_mismatch 指标。"""
        owner_tid = uuid4()
        att = _make_attachment(owner_tid)
        validator, _ = _make_validator(att)

        with patch(
            "app.modules.finance.attachment_validator.sentry_sdk"
        ), patch(
            "app.modules.finance.attachment_validator.AsyncSessionBypass"
        ), patch.object(
            validator, "_record_failure"
        ) as mock_record:
            with pytest.raises(InvalidAttachmentReferenceError):
                await validator.validate(
                    session=MagicMock(),
                    attachment_id=att.id,
                    tenant_id=uuid4(),
                )
            # tenant_mismatch 至少被记录一次
            recorded = [c.args[0] for c in mock_record.call_args_list]
            assert "tenant_mismatch" in recorded
