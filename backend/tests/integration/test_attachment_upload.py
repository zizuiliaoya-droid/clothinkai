"""U05 集成测试：shared attachment 基础设施（upload record + status 状态机 + 跨租户）。

create_upload_record 依赖 R2 presigned URL（需 R2 配置），本测试聚焦可在
无 R2 环境验证的部分：
- 直接落 Attachment 行 → mark_uploaded 状态机（uploading → ready）
- mark_uploaded 跨租户防护（WHERE tenant_id）
- mark_uploaded 重复调用（非 uploading 状态）抛错
- get_by_id 读取
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import AttachmentService
from app.core.exceptions import AttachmentError
from app.core.tenancy import tenant_id_ctx


@pytest.mark.integration
@pytest.mark.asyncio
class TestMarkUploaded:
    async def test_uploading_to_ready(
        self,
        session: AsyncSession,
        tenant_a: Any,
        attachment_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            att = await attachment_factory.attachment(status="uploading")
            svc = AttachmentService()
            updated = await svc.mark_uploaded(
                session=session, attachment_id=att.id, tenant_id=tenant_a.id
            )
            assert updated.status == "ready"
        finally:
            tenant_id_ctx.reset(token)

    async def test_cross_tenant_mark_uploaded_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        attachment_factory: Any,
    ) -> None:
        """WHERE tenant_id 防越权：tenant_b 不能 mark tenant_a 的附件。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            att = await attachment_factory.attachment(status="uploading")
        finally:
            tenant_id_ctx.reset(token)

        svc = AttachmentService()
        with pytest.raises(AttachmentError):
            await svc.mark_uploaded(
                session=session, attachment_id=att.id, tenant_id=tenant_b.id
            )

    async def test_double_mark_uploaded_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        attachment_factory: Any,
    ) -> None:
        """已 ready 的附件再次 mark → 抛错（状态机防重复）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            att = await attachment_factory.attachment(status="ready")
            svc = AttachmentService()
            with pytest.raises(AttachmentError):
                await svc.mark_uploaded(
                    session=session,
                    attachment_id=att.id,
                    tenant_id=tenant_a.id,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestGetById:
    async def test_get_existing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        attachment_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            att = await attachment_factory.attachment()
            svc = AttachmentService()
            found = await svc.get_by_id(
                session=session, attachment_id=att.id
            )
            assert found is not None
            assert found.id == att.id
            assert found.purpose == "settlement_proof"
        finally:
            tenant_id_ctx.reset(token)

    async def test_get_missing_returns_none(
        self, session: AsyncSession
    ) -> None:
        svc = AttachmentService()
        found = await svc.get_by_id(session=session, attachment_id=uuid4())
        assert found is None
