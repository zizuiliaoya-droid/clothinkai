"""U03 blogger 服务层。

按 nfr-design-patterns.md：
- P-U03-01：单字段 GIN trgm + 防侧信道（service 层根据角色决定 include_wechat_in_keyword）
- P-U03-02：GIN JSONB tag 包含查询
- 复用 U02 P-U02-02 字段权限硬编码（QUOTE_VISIBLE_ROLES + CONTACT_VISIBLE_ROLES）
- 复用 U02 P-U02-03 数据库原子 upsert
- 复用 U02 P-U02-04 软删 + 引用检查（U03 占位）
- match 降级语义：业务未匹配 200 + 空数组 / 系统失败异常冒泡（不 try/except）

U10b 4 个钩子方法占位（NotImplementedError）：
- recompute_blogger_type / recompute_quality_tags / mark_suspected_fake / bulk_recompute_tags
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import blogger_search_results_count
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.domain import (
    build_blogger_audit_changes,
    compute_blogger_changes,
)
from app.modules.blogger.exceptions import (
    BloggerHasReferenceError,
    BloggerNotFoundError,
    BloggerXhsIdConflictError,
    FieldPermissionDenied,
)
from app.modules.blogger.models import Blogger
from app.modules.blogger.repository import BloggerListFilters, BloggerRepository
from app.modules.blogger.schemas import (
    BloggerCreate,
    BloggerPage,
    BloggerResponse,
    BloggerUpdate,
)
from app.modules.blogger.tag_service import BloggerTagService


class BloggerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)
        self._tags = BloggerTagService(session)
        # 注：U04 时增加 promotion_repo: PromotionRepository

    # ============================================================
    # CRUD
    # ============================================================

    async def create_blogger(
        self, payload: BloggerCreate, user: User
    ) -> BloggerResponse:
        # BR-U03-01: 唯一性
        existing = await self._repo.get_by_xiaohongshu_id(payload.xiaohongshu_id)
        if existing is not None:
            raise BloggerXhsIdConflictError(
                "该博主已存在，是否查看？",
                details={
                    "xiaohongshu_id": payload.xiaohongshu_id,
                    "existing_blogger_id": str(existing.id),
                },
            )

        # BR-U03-42: 字段写权限
        await self._check_sensitive_write_permission(payload, user)

        blogger = Blogger(
            xiaohongshu_id=payload.xiaohongshu_id,
            nickname=payload.nickname,
            platform=payload.platform.value,
            wechat=payload.wechat,
            phone=payload.phone,
            follower_count=payload.follower_count,
            blogger_type=payload.blogger_type.value if payload.blogger_type else None,
            gender_target=(
                payload.gender_target.value if payload.gender_target else None
            ),
            category_tags=list(payload.category_tags),
            quality_tags=list(payload.quality_tags),
            quote=payload.quote,
            cooperation_history=payload.cooperation_history,
            remark=payload.remark,
            is_suspected_fake=payload.is_suspected_fake,
        )
        # U11 BR-U11-01: follower_count 提供时自动按阈值分级 blogger_type
        if payload.follower_count is not None:
            blogger.blogger_type = self._tags.compute_blogger_type(
                payload.follower_count
            )
        self._repo.add(blogger)
        await self._session.flush()

        # 审计：BR-U03-32 创建仅记 xiaohongshu_id + nickname（敏感值脱敏）
        after: dict[str, Any] = {
            "xiaohongshu_id": blogger.xiaohongshu_id,
            "nickname": blogger.nickname,
        }
        if blogger.quote is not None:
            after["quote_changed"] = True
        if blogger.wechat is not None:
            after["wechat_changed"] = True
        if blogger.phone is not None:
            after["phone_changed"] = True
        await self._audit.log(
            action="blogger.create",
            resource="blogger",
            resource_id=blogger.id,
            after=after,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(blogger, user)

    async def update_blogger(
        self, blogger_id: UUID, payload: BloggerUpdate, user: User
    ) -> BloggerResponse:
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")

        # BR-U03-01: 改 xiaohongshu_id 时唯一性
        if (
            "xiaohongshu_id" in payload.model_fields_set
            and payload.xiaohongshu_id is not None
            and payload.xiaohongshu_id != blogger.xiaohongshu_id
        ):
            existing = await self._repo.get_by_xiaohongshu_id(payload.xiaohongshu_id)
            if existing is not None:
                raise BloggerXhsIdConflictError(
                    f"小红书 ID {payload.xiaohongshu_id} 已被使用",
                    details={
                        "xiaohongshu_id": payload.xiaohongshu_id,
                        "existing_blogger_id": str(existing.id),
                    },
                )

        # BR-U03-42: 字段写权限
        await self._check_sensitive_write_permission(payload, user)

        changes = compute_blogger_changes(blogger, payload)
        if not changes:
            return await self._to_response(blogger, user)

        # 应用变更
        for field in changes:
            new_value = getattr(payload, field)
            if field in {"platform", "blogger_type", "gender_target"}:
                # Enum → str 存储
                new_value = new_value.value if new_value is not None else None
            setattr(blogger, field, new_value)

        # U11 BR-U11-01: follower_count 变更时自动重算 blogger_type
        if "follower_count" in changes:
            blogger.blogger_type = self._tags.compute_blogger_type(
                blogger.follower_count
            )

        await self._session.flush()

        # 审计：BR-U03-30 仅敏感字段写 audit + 敏感值脱敏
        audit_changes = build_blogger_audit_changes(changes)
        if audit_changes:
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for k, v in audit_changes.items():
                if isinstance(v, dict):
                    before[k] = v["before"]
                    after[k] = v["after"]
                else:
                    after[k] = v  # 脱敏标记
            await self._audit.log(
                action="blogger.update",
                resource="blogger",
                resource_id=blogger.id,
                before=before or None,
                after=after,
                user_id=user.id,
            )
        await self._session.commit()
        return await self._to_response(blogger, user)

    async def upsert_by_xiaohongshu_id(
        self, payload: BloggerCreate, user: User
    ) -> BloggerResponse:
        """U06c 导入路径：数据库原子 upsert.

        与 partial UNIQUE 严格对齐 + 不"恢复"软删行 + audit 区分入口。
        不暴露 HTTP；U06c 通过 ``from app.modules.blogger.service import BloggerService`` 调用。
        """
        # 字段写权限（与 create 完全相同）
        await self._check_sensitive_write_permission(payload, user)

        values: dict[str, Any] = {
            "xiaohongshu_id": payload.xiaohongshu_id,
            "nickname": payload.nickname,
            "platform": payload.platform.value,
            "wechat": payload.wechat,
            "phone": payload.phone,
            "follower_count": payload.follower_count,
            "blogger_type": (
                payload.blogger_type.value if payload.blogger_type else None
            ),
            "gender_target": (
                payload.gender_target.value if payload.gender_target else None
            ),
            "category_tags": list(payload.category_tags),
            "quality_tags": list(payload.quality_tags),
            "quote": payload.quote,
            "cooperation_history": payload.cooperation_history,
            "remark": payload.remark,
            "is_suspected_fake": payload.is_suspected_fake,
            "is_active": True,
            "is_deleted": False,
        }

        blogger, is_inserted = await self._repo.upsert_atomic(
            tenant_id=user.tenant_id, values=values
        )

        # 审计区分入口
        action = (
            "blogger.create_via_import"
            if is_inserted
            else "blogger.update_via_import"
        )
        after_marker: dict[str, Any] = {
            "xiaohongshu_id": blogger.xiaohongshu_id,
            "nickname": blogger.nickname,
        }
        if blogger.quote is not None:
            after_marker["quote_changed"] = True
        if blogger.wechat is not None:
            after_marker["wechat_changed"] = True
        if blogger.phone is not None:
            after_marker["phone_changed"] = True
        await self._audit.log(
            action=action,
            resource="blogger",
            resource_id=blogger.id,
            after=after_marker,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(blogger, user)

    async def soft_delete_blogger(self, blogger_id: UUID, user: User) -> None:
        """BR-U03-20: 软删 + 引用检查."""
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")

        refs = await self.check_references(blogger_id)
        if sum(refs.values()) > 0:
            raise BloggerHasReferenceError(
                f"该博主已被引用（{refs}），仅可停用",
                details=refs,
            )

        blogger.is_deleted = True
        blogger.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="blogger.delete",
            resource="blogger",
            resource_id=blogger.id,
            user_id=user.id,
        )
        await self._session.commit()

    async def disable_blogger(
        self, blogger_id: UUID, user: User
    ) -> BloggerResponse:
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        blogger.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="blogger.disable",
            resource="blogger",
            resource_id=blogger.id,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(blogger, user)

    async def restore_blogger(
        self, blogger_id: UUID, user: User
    ) -> BloggerResponse:
        """BR-U03-21: 恢复软删."""
        blogger = await self._repo.get_by_id(blogger_id, include_deleted=True)
        if blogger is None or not blogger.is_deleted:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在或未被软删")

        # 校验 xiaohongshu_id 是否被新博主占用
        existing = await self._repo.get_by_xiaohongshu_id(blogger.xiaohongshu_id)
        if existing is not None and existing.id != blogger.id:
            raise BloggerXhsIdConflictError(
                f"小红书 ID {blogger.xiaohongshu_id} 已被新博主占用，请先重命名",
                details={
                    "xiaohongshu_id": blogger.xiaohongshu_id,
                    "existing_blogger_id": str(existing.id),
                },
            )

        blogger.is_deleted = False
        blogger.is_active = True
        await self._session.flush()
        await self._audit.log(
            action="blogger.restore",
            resource="blogger",
            resource_id=blogger.id,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(blogger, user)

    # ============================================================
    # Read
    # ============================================================

    async def get_blogger(self, blogger_id: UUID, user: User) -> BloggerResponse:
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        return await self._to_response(blogger, user)

    async def list_bloggers(
        self,
        *,
        filters: BloggerListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> BloggerPage:
        """搜索 + 分页（含防侧信道 P-U03-01）.

        关键：根据 user 角色决定 include_wechat_in_keyword 参数。
        系统失败让异常自然冒泡（不 try/except DB 异常 → 5xx + Sentry）。
        """
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_search_contact = can_read_field("blogger", "wechat", ctx)

        items, total = await self._repo.list(
            filters=filters,
            page=page,
            page_size=page_size,
            include_wechat_in_keyword=can_search_contact,
        )

        # 监控候选数分布
        blogger_search_results_count.observe(total)

        return BloggerPage(
            items=[await self._to_response(b, user) for b in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ============================================================
    # 引用检查（U04 启用）
    # ============================================================

    async def check_references(self, blogger_id: UUID) -> dict[str, int]:
        """U03 阶段：promotion 表不存在，返回零引用。

        TODO U04: 改为 ``await self._promotion_repo.count_by_blogger(blogger_id)``
        """
        _ = blogger_id
        return {"promotion_count": 0}

    # ============================================================
    # U11 标签计算（替换 U10b 占位钩子）
    # ============================================================

    async def recompute_blogger_type(self, blogger_id: UUID) -> Blogger:
        """U11: 按 follower_count 自动重算 blogger_type."""
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        blogger.blogger_type = self._tags.compute_blogger_type(
            blogger.follower_count
        )
        await self._session.flush()
        await self._session.commit()
        return blogger

    async def recompute_quality_tags(self, blogger_id: UUID) -> Blogger:
        """U11: 聚合 promotion 历史自动重算质量标签 + 假号嫌疑."""
        from app.services.metric.blogger_quality import compute_quality_tags

        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        blogger.quality_tags = await compute_quality_tags(
            blogger.id, self._session, blogger.tenant_id
        )
        ratio = self._tags.compute_read_like_ratio(blogger.audience_profile)
        blogger.is_suspected_fake = self._tags.is_fake_account(ratio)
        await self._session.flush()
        await self._session.commit()
        return blogger

    async def mark_suspected_fake(
        self, blogger_id: UUID, reason: str
    ) -> Blogger:
        """U11: 按 read_like_ratio 判定假号嫌疑（reason 记审计）."""
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        ratio = self._tags.compute_read_like_ratio(blogger.audience_profile)
        blogger.is_suspected_fake = self._tags.is_fake_account(ratio)
        await self._session.flush()
        await self._audit.log(
            action="blogger.mark_suspected_fake",
            resource="blogger",
            resource_id=blogger.id,
            after={"is_suspected_fake": blogger.is_suspected_fake, "reason": reason},
        )
        await self._session.commit()
        return blogger

    async def recompute_tags_for_current_tenant(
        self, tenant_id: UUID
    ) -> dict[str, int]:
        """U11: recompute 端点同步入口 —— 重算当前租户全部活跃博主标签."""
        result = await self._tags.recompute_for_tenant(tenant_id)
        await self._session.commit()
        return result

    async def bulk_recompute_tags(self) -> int:
        """U11: Celery 批量入口（按当前 tenant_id 上下文）。返回处理博主数."""
        from app.core.tenancy import tenant_id_ctx

        tid = tenant_id_ctx.get()
        result = await self._tags.recompute_for_tenant(tid)
        await self._session.commit()
        return result["updated"]

    # ============================================================
    # Private helpers
    # ============================================================

    async def _check_sensitive_write_permission(
        self, payload: BloggerCreate | BloggerUpdate, user: User
    ) -> None:
        """BR-U03-42 / U09: 字段写权限（经 core 注册表 + 字段级 override）。"""
        fields_set = payload.model_fields_set
        sensitive_set = fields_set & {"quote", "wechat", "phone"}
        if not sensitive_set:
            return

        ctx = await build_field_perm_context(user.id, self._roles, self._perms)

        for field_name in ("quote", "wechat", "phone"):
            if field_name in sensitive_set:
                value = getattr(payload, field_name)
                if value is not None and not can_write_field(
                    "blogger", field_name, ctx
                ):
                    raise FieldPermissionDenied(field=field_name, entity="blogger")

    async def _to_response(self, blogger: Blogger, user: User) -> BloggerResponse:
        """BR-U03-41 / U09: 字段读过滤（经 core 注册表 + 字段级 override）。"""
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_quote = can_read_field("blogger", "quote", ctx)
        can_see_wechat = can_read_field("blogger", "wechat", ctx)
        can_see_phone = can_read_field("blogger", "phone", ctx)

        return BloggerResponse(
            id=blogger.id,
            xiaohongshu_id=blogger.xiaohongshu_id,
            nickname=blogger.nickname,
            platform=blogger.platform,
            wechat=blogger.wechat if can_see_wechat else None,
            phone=blogger.phone if can_see_phone else None,
            follower_count=blogger.follower_count,
            blogger_type=blogger.blogger_type,
            gender_target=blogger.gender_target,
            category_tags=list(blogger.category_tags or []),
            quality_tags=list(blogger.quality_tags or []),
            quote=blogger.quote if can_see_quote else None,
            cooperation_history=blogger.cooperation_history,
            remark=blogger.remark,
            is_suspected_fake=blogger.is_suspected_fake,
            is_active=blogger.is_active,
            is_deleted=blogger.is_deleted,
            audience_profile=blogger.audience_profile,
            read_like_ratio=self._tags.compute_read_like_ratio(
                blogger.audience_profile
            ),
            created_at=blogger.created_at,
            updated_at=blogger.updated_at,
        )


__all__ = ["BloggerService"]
