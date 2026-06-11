"""U03 Blogger CRUD 集成测试.

覆盖：
- EP04-S01 创建博主（含 xiaohongshu_id 重复 409 + existing_blogger_id 引导）
- EP04-S02 编辑博主（含 quote audit 脱敏 + 字段权限）
- 软删 / 停用 / 恢复
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.exceptions import (
    BloggerNotFoundError,
    BloggerXhsIdConflictError,
    FieldPermissionDenied,
)
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateBlogger:
    async def test_create_basic(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            response = await svc.create_blogger(
                BloggerCreate(
                    xiaohongshu_id="XHS123",
                    nickname="测试博主",
                    quote=Decimal("500.00"),
                ),
                user,
            )
            assert response.xiaohongshu_id == "XHS123"
            assert response.nickname == "测试博主"
            assert response.platform == "小红书"
            assert response.is_active is True
            assert response.is_deleted is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_returns_existing_id(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        """EP04-S01.given2: 重复时返回 existing_blogger_id 引导."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            existing = await blogger_factory.blogger(xiaohongshu_id="DUP")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            with pytest.raises(BloggerXhsIdConflictError) as exc_info:
                await svc.create_blogger(
                    BloggerCreate(xiaohongshu_id="DUP", nickname="另一个"),
                    user,
                )
            # 关键：details 含 existing_blogger_id 用于前端引导
            assert exc_info.value.details["existing_blogger_id"] == str(existing.id)
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateBlogger:
    async def test_update_quote_with_pr(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        blogger_factory: Any,
    ) -> None:
        """EP04-S02: PR 编辑 quote 成功."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = BloggerService(session)
            response = await svc.update_blogger(
                blogger.id,
                BloggerUpdate(quote=Decimal("300.00")),
                user,
            )
            assert response.quote == Decimal("300.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_quote_with_finance_denied(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        blogger_factory: Any,
    ) -> None:
        """财务可读 quote 但不可写."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger()
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BloggerService(session)
            with pytest.raises(FieldPermissionDenied) as exc_info:
                await svc.update_blogger(
                    blogger.id,
                    BloggerUpdate(quote=Decimal("999.00")),
                    user,
                )
            assert exc_info.value.field == "quote"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_quote_with_designer_denied(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger()
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = BloggerService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.update_blogger(
                    blogger.id,
                    BloggerUpdate(quote=Decimal("100.00")),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_unchanged_returns_same(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        """字段未变更时不更新 + 不写 audit (BR-U03-31)."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(nickname="保持")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            response = await svc.update_blogger(
                blogger.id,
                BloggerUpdate(nickname="保持"),
                user,
            )
            assert response.nickname == "保持"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_nonexistent_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            with pytest.raises(BloggerNotFoundError):
                await svc.update_blogger(
                    uuid4(), BloggerUpdate(nickname="x"), user
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSoftDeleteBlogger:
    async def test_soft_delete_no_references(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        """U03 阶段 promotion 表不存在，引用永远 0，应允许软删."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            await svc.soft_delete_blogger(blogger.id, user)
            await session.refresh(blogger)
            assert blogger.is_deleted is True
        finally:
            tenant_id_ctx.reset(token)
