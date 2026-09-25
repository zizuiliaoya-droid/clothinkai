"""页面筛选记忆（PRD 第 7 章 user_filter_pref，复用 U17 user_preference 表）。

重点覆盖：白名单拦截未知页面编码、整块覆盖而非合并、用户之间互不可见、载荷体积上限。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.report.user_preference_service import (
    FILTER_PAGE_CODES,
    MAX_PREF_BYTES,
    UserPreferenceService,
    validate_filter_payload,
)


class TestValidateFilterPayload:
    def test_rejects_unknown_page_code(self) -> None:
        with pytest.raises(ValidationError):
            validate_filter_payload("../etc/passwd", {})

    def test_accepts_whitelisted_codes(self) -> None:
        for code in FILTER_PAGE_CODES:
            validate_filter_payload(code, {"season": ["2026秋"]})

    def test_rejects_oversized_payload(self) -> None:
        """JSONB 列本身没有长度限制，不设上限等于给了一张任人塞数据的表。"""
        with pytest.raises(ValidationError):
            validate_filter_payload("product_roi", {"junk": "x" * (MAX_PREF_BYTES + 1)})


@pytest.mark.integration
@pytest.mark.asyncio
class TestFilterRoundtrip:
    async def test_unsaved_returns_empty_dict(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            assert await svc.get_filter(user.id, "product_roi") == {}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_save_then_read_back(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            payload = {"season": ["2026秋", "2026冬"], "category": ["上衣"], "preset": "last_30d"}
            await svc.save_filter(user, "product_roi", payload)
            assert await svc.get_filter(user.id, "product_roi") == payload
        finally:
            tenant_id_ctx.reset(tok)

    async def test_save_replaces_instead_of_merging(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        """用户清掉某个筛选项后它就该消失；合并语义会让被清掉的条件复活。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            await svc.save_filter(user, "product_roi", {"season": ["2026秋"], "category": ["上衣"]})
            await svc.save_filter(user, "product_roi", {"season": ["2026冬"]})
            assert await svc.get_filter(user.id, "product_roi") == {"season": ["2026冬"]}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_page_codes_are_independent(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            await svc.save_filter(user, "product_roi", {"a": 1})
            await svc.save_filter(user, "pr_work_progress", {"b": 2})
            assert await svc.get_filter(user.id, "product_roi") == {"a": 1}
            assert await svc.get_filter(user.id, "pr_work_progress") == {"b": 2}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_users_do_not_share_filters(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            alice = await factory.user(tenant_a, roles=[admin_role])
            bob = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            await svc.save_filter(alice, "product_roi", {"season": ["2026秋"]})
            assert await svc.get_filter(bob.id, "product_roi") == {}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bi_layout_key_not_clobbered_by_filters(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        """筛选 key 带 filter: 前缀，不会和 bi_layout 这类偏好撞车。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UserPreferenceService(session)
            await svc.upsert(user, "bi_layout", {"cards": ["a"]})
            await svc.save_filter(user, "bi_dashboard", {"preset": "last_7d"})
            assert await svc.get_or_default(user.id, "bi_layout", {}) == {"cards": ["a"]}
            assert await svc.get_filter(user.id, "bi_dashboard") == {"preset": "last_7d"}
        finally:
            tenant_id_ctx.reset(tok)
