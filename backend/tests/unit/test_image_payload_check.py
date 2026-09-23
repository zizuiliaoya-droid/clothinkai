"""图片上传载荷校验（``check_image_payload``）。

只信 MIME 声明是不够的：任意文件改个扩展名就能当图片存进私有桶。这里覆盖
魔数核对、体积上限、空文件与文件名长度，收款码与款式主图共用同一套判定。
"""

from __future__ import annotations

import pytest

from app.core.attachment import ALLOWED_PURPOSES, check_image_payload

JPEG = b"\xff\xd8\xff" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32

MB = 1024 * 1024


class TestAcceptsValidImages:
    @pytest.mark.parametrize(
        ("data", "mime"),
        [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
    )
    def test_accepts_matching_signature(self, data: bytes, mime: str) -> None:
        assert (
            check_image_payload(data=data, mime_type=mime, filename="x", max_bytes=10 * MB) is None
        )

    def test_filename_may_be_absent(self) -> None:
        assert (
            check_image_payload(data=PNG, mime_type="image/png", filename=None, max_bytes=10 * MB)
            is None
        )


class TestRejectsBadPayloads:
    def test_rejects_unsupported_mime(self) -> None:
        reason = check_image_payload(
            data=b"%PDF-1.4", mime_type="application/pdf", filename="x", max_bytes=10 * MB
        )
        assert reason is not None
        assert "JPG" in reason

    def test_rejects_missing_mime(self) -> None:
        assert (
            check_image_payload(data=PNG, mime_type=None, filename="x", max_bytes=10 * MB)
            is not None
        )

    def test_rejects_empty_data(self) -> None:
        reason = check_image_payload(
            data=b"", mime_type="image/png", filename="x", max_bytes=10 * MB
        )
        assert reason is not None
        assert "不能为空" in reason

    def test_rejects_oversize(self) -> None:
        reason = check_image_payload(
            data=PNG + b"\x00" * MB, mime_type="image/png", filename="x", max_bytes=1
        )
        assert reason is not None

    def test_rejects_long_filename(self) -> None:
        reason = check_image_payload(
            data=PNG, mime_type="image/png", filename="a" * 256, max_bytes=10 * MB
        )
        assert reason is not None
        assert "255" in reason

    def test_rejects_signature_mismatch(self) -> None:
        """声明 PNG 实际是 JPEG —— 必须拦住，否则改扩展名就能绕过。"""
        reason = check_image_payload(
            data=JPEG, mime_type="image/png", filename="x", max_bytes=10 * MB
        )
        assert reason is not None
        assert "不一致" in reason

    def test_rejects_webp_with_wrong_offset(self) -> None:
        """WebP 的 'WEBP' 标记在偏移 8，只看开头的 RIFF 是不够的。"""
        reason = check_image_payload(
            data=b"RIFF" + b"\x00" * 16, mime_type="image/webp", filename="x", max_bytes=10 * MB
        )
        assert reason is not None

    def test_rejects_truncated_webp(self) -> None:
        reason = check_image_payload(
            data=b"RIFF", mime_type="image/webp", filename="x", max_bytes=10 * MB
        )
        assert reason is not None


class TestPurposeWhitelist:
    def test_order_adjustment_qr_purpose_registered(self) -> None:
        """purpose 未进白名单时 create_upload_record 会拒绝，属于易漏的一步。"""
        assert "order_adjustment_payment_qr" in ALLOWED_PURPOSES
        assert "promotion_payment_qr" in ALLOWED_PURPOSES
