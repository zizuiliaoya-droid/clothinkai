"""U03 blogger 服务层。

按 nfr-design-patterns.md：
- P-U03-01：单字段 GIN trgm + 防侧信道（service 层根据角色决定 include_wechat_in_keyword）
- P-U03-02：GIN JSONB tag 包含查询
- 复用 U02 P-U02-02 字段权限硬编码（QUOTE_VISIBLE_ROLES + CONTACT_VISIBLE_ROLES）
- 复用 U02 P-U02-03 数据库原子 upsert
- 复用 U02 P-U02-04 软删 + 推广历史引用检查
- match 降级语义：业务未匹配 200 + 空数组 / 系统失败异常冒泡（不 try/except）
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
    BloggerSystemTagReadonlyError,
    BloggerTagNotInDictError,
    BloggerXhsIdConflictError,
    FieldPermissionDenied,
    InvalidAccountFormatError,
)
from app.modules.blogger.models import Blogger
from app.modules.blogger.repository import BloggerListFilters, BloggerRepository
from app.modules.blogger.schemas import (
    ACCOUNT_FORMAT_ERROR,
    BloggerCreate,
    BloggerPage,
    BloggerResponse,
    BloggerUpdate,
    is_valid_account,
)
from app.modules.blogger.tag_config import SYSTEM_TAGS, TYPE_GRADED_PLATFORMS
from app.modules.blogger.tag_dict import BloggerTagDictService
from app.modules.blogger.tag_service import BloggerTagService
from app.modules.promotion.repository import PromotionRepository


class BloggerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)
        self._tags = BloggerTagService(session)
        self._tag_dict = BloggerTagDictService(session)
        self._promotion_repo = PromotionRepository(session)

    # ============================================================
    # CRUD
    # ============================================================

    async def create_blogger(self, payload: BloggerCreate, user: User) -> BloggerResponse:
        # BR-U03-01 / 8b-1: （平台, 账号）唯一
        await self._ensure_account_free(payload.platform.value, payload.xiaohongshu_id)

        # BR-U03-42: 字段写权限
        await self._check_sensitive_write_permission(payload, user)
        await self._check_quote_note_write(payload, user)
        # 8b-3：系统标签只读、类目标签按字典
        await self._check_tags(payload, None, user)

        blogger = Blogger(
            xiaohongshu_id=payload.xiaohongshu_id,
            nickname=payload.nickname,
            platform=payload.platform.value,
            level=payload.level,
            content_category=payload.content_category,
            contact_primary=payload.contact_primary,
            contact_primary_added=payload.contact_primary_added,
            contact_backup=payload.contact_backup,
            contact_backup_added=payload.contact_backup_added,
            wechat=payload.wechat,
            phone=payload.phone,
            follower_count=payload.follower_count,
            blogger_type=payload.blogger_type.value if payload.blogger_type else None,
            gender_target=(payload.gender_target.value if payload.gender_target else None),
            category_tags=list(payload.category_tags),
            quality_tags=list(payload.quality_tags),
            quote=payload.quote,
            cooperation_history=payload.cooperation_history,
            remark=payload.remark,
            is_suspected_fake=payload.is_suspected_fake,
            web_id=payload.web_id,
            homepage_url=payload.homepage_url,
            quote_note=payload.quote_note,
        )
        # U11 BR-U11-01: follower_count 提供时自动按阈值分级 blogger_type（8b：只对分级平台）
        if payload.follower_count is not None and blogger.platform in TYPE_GRADED_PLATFORMS:
            blogger.blogger_type = self._tags.compute_blogger_type(payload.follower_count)
        self._repo.add(blogger)
        await self._session.flush()

        # 审计：BR-U03-32 创建仅记账号 + 昵称 + 平台（8b 判重键带平台；敏感值脱敏）
        after: dict[str, Any] = {
            "xiaohongshu_id": blogger.xiaohongshu_id,
            "nickname": blogger.nickname,
            "platform": blogger.platform,
        }
        if blogger.quote is not None:
            after["quote_changed"] = True
        if blogger.quote_note is not None:
            after["quote_note_changed"] = True
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

        # BR-U03-01 / 8b-1: 平台或账号任一改了 → 新（平台, 账号）唯一（排除自己）
        fields_set = payload.model_fields_set
        new_platform = (
            payload.platform.value
            if "platform" in fields_set and payload.platform is not None
            else blogger.platform
        )
        # 账号没变（没带 / 与库里相同，库里首尾空格不算差别）→ 不校验格式、不改库里原值：
        # 历史账号含中文（昵称当账号导进来的），前端编辑时会原样带上
        account_changed = (
            "xiaohongshu_id" in fields_set
            and payload.xiaohongshu_id is not None
            and payload.xiaohongshu_id != (blogger.xiaohongshu_id or "").strip()
        )
        new_account = (
            payload.xiaohongshu_id
            if account_changed and payload.xiaohongshu_id is not None
            else blogger.xiaohongshu_id
        )
        if account_changed and not is_valid_account(new_account):
            raise InvalidAccountFormatError(
                ACCOUNT_FORMAT_ERROR, details={"field": "xiaohongshu_id"}
            )
        if (new_platform, new_account) != (blogger.platform, blogger.xiaohongshu_id):
            await self._ensure_account_free(new_platform, new_account, exclude_id=blogger.id)

        # BR-U03-42: 字段写权限
        await self._check_sensitive_write_permission(payload, user)
        await self._check_quote_note_write(payload, user)
        # 8b-3：系统标签只读、类目标签按字典
        await self._check_tags(payload, blogger, user)

        changes = compute_blogger_changes(blogger, payload)
        if not account_changed:
            changes.pop("xiaohongshu_id", None)
        if not changes:
            return await self._to_response(blogger, user)

        # 应用变更
        for field in changes:
            new_value = getattr(payload, field)
            if field in {"platform", "blogger_type", "gender_target"}:
                # Enum → str 存储
                new_value = new_value.value if new_value is not None else None
            setattr(blogger, field, new_value)

        # U11 BR-U11-01: follower_count 变更时自动重算 blogger_type（8b：只对分级平台）
        # 只改平台：新平台分级且有粉丝数就重算；不分级（抖音）或没有粉丝数就保留原值
        if blogger.platform in TYPE_GRADED_PLATFORMS and (
            "follower_count" in changes
            or ("platform" in changes and blogger.follower_count is not None)
        ):
            blogger.blogger_type = self._tags.compute_blogger_type(blogger.follower_count)

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

    async def upsert_by_xiaohongshu_id(self, payload: BloggerCreate, user: User) -> BloggerResponse:
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
            "blogger_type": (payload.blogger_type.value if payload.blogger_type else None),
            "gender_target": (payload.gender_target.value if payload.gender_target else None),
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
        action = "blogger.create_via_import" if is_inserted else "blogger.update_via_import"
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

    async def disable_blogger(self, blogger_id: UUID, user: User) -> BloggerResponse:
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

    async def restore_blogger(self, blogger_id: UUID, user: User) -> BloggerResponse:
        """BR-U03-21: 恢复软删."""
        blogger = await self._repo.get_by_id(blogger_id, include_deleted=True)
        if blogger is None or not blogger.is_deleted:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在或未被软删")

        # 校验（平台, 账号）是否被新博主占用
        await self._ensure_account_free(
            blogger.platform, blogger.xiaohongshu_id, exclude_id=blogger.id
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
    # 历史引用检查
    # ============================================================

    async def check_references(self, blogger_id: UUID) -> dict[str, int]:
        """检查博主的全部历史推广引用；租户隔离由 RLS 保证。"""
        return {"promotion_count": await self._promotion_repo.count_by_blogger(blogger_id)}

    # ============================================================
    # U11 标签计算
    # ============================================================

    async def recompute_blogger_type(self, blogger_id: UUID) -> Blogger:
        """U11: 按 follower_count 自动重算 blogger_type（8b：只对分级平台，其余不动）."""
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(f"博主 {blogger_id} 不存在")
        if blogger.platform in TYPE_GRADED_PLATFORMS:
            blogger.blogger_type = self._tags.compute_blogger_type(blogger.follower_count)
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

    async def mark_suspected_fake(self, blogger_id: UUID, reason: str) -> Blogger:
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

    async def recompute_tags_for_current_tenant(self, tenant_id: UUID) -> dict[str, int]:
        """U11: recompute 端点同步入口 —— 重算当前租户全部活跃博主标签."""
        result = await self._tags.recompute_for_tenant(tenant_id)
        await self._session.commit()
        return result

    async def bulk_recompute_tags(self) -> int:
        """U11: Celery 批量入口（按当前 tenant_id 上下文）。返回处理博主数."""
        from app.core.exceptions import TenantContextMissingError
        from app.core.tenancy import tenant_id_ctx

        tid = tenant_id_ctx.get()
        if tid is None:
            # 与 auth/service.py 一致：缺租户上下文即显式失败，不把 None 往下传
            raise TenantContextMissingError()
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
                if value is not None and not can_write_field("blogger", field_name, ctx):
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
            level=blogger.level,
            content_category=blogger.content_category,
            contact_primary=blogger.contact_primary,
            contact_primary_added=blogger.contact_primary_added,
            contact_backup=blogger.contact_backup,
            contact_backup_added=blogger.contact_backup_added,
            is_added_success=bool(blogger.contact_primary_added or blogger.contact_backup_added),
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
            read_like_ratio=self._tags.compute_read_like_ratio(blogger.audience_profile),
            crawler_metrics=dict(blogger.crawler_metrics or {}),
            web_id=blogger.web_id,
            homepage_url=blogger.homepage_url,
            platform_metrics=blogger.platform_metrics,
            # 8b D3：报价备注与报价同一条读权限
            quote_note=blogger.quote_note if can_see_quote else None,
            created_at=blogger.created_at,
            updated_at=blogger.updated_at,
        )

    async def _ensure_account_free(
        self, platform: str, account: str, *, exclude_id: UUID | None = None
    ) -> None:
        """8b-1：（平台, 账号）被别的未删除博主占用 → 409（code 不改，前端已在用）。"""
        existing = await self._repo.get_by_account(platform, account)
        if existing is not None and existing.id != exclude_id:
            raise BloggerXhsIdConflictError(
                f"{platform} 已有账号 {account} 的博主",
                details={
                    "platform": platform,
                    "xiaohongshu_id": account,
                    "existing_blogger_id": str(existing.id),
                },
            )

    async def _check_quote_note_write(
        self, payload: BloggerCreate | BloggerUpdate, user: User
    ) -> None:
        """8b D3：报价备注的写权限与报价完全相同（同一条 ``("blogger", "quote")`` 规则与个人授权）。

        与报价一样只挡写入非空值。
        """
        if "quote_note" not in payload.model_fields_set or payload.quote_note is None:
            return
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_write_field("blogger", "quote", ctx):
            raise FieldPermissionDenied(field="quote_note", entity="blogger")

    async def _check_tags(
        self, payload: BloggerCreate | BloggerUpdate, blogger: Blogger | None, user: User
    ) -> None:
        """8b-3（设计 §5.4）：系统标签只读、类目标签新加的词要在启用字典里。

        - ``quality_tags`` 只由重算写：create 传了非空、update 传了且归一后与库里不同 → 422
        - ``category_tags`` 只看新加的（新值 − 库里的值）：含系统标签词 → 422；不在启用字典 → 422。
          旧标签（含字典里已删的）可留可去
        """
        fields_set = payload.model_fields_set
        if "quality_tags" in fields_set:
            new_quality = set(payload.quality_tags or [])
            old_quality = set(blogger.quality_tags or []) if blogger is not None else set()
            if new_quality != old_quality:
                raise BloggerSystemTagReadonlyError(
                    "质量标签是系统标签，由重算自动打，不能手工修改",
                    details={"field": "quality_tags"},
                )

        if "category_tags" not in fields_set:
            return
        old_category = set(blogger.category_tags or []) if blogger is not None else set()
        added = [t for t in dict.fromkeys(payload.category_tags or []) if t not in old_category]
        if not added:
            return
        system_words = [t for t in added if t in SYSTEM_TAGS]
        if system_words:
            raise BloggerSystemTagReadonlyError(
                f"{'、'.join(system_words)} 是系统标签，不能手工加",
                details={"field": "category_tags", "tags": system_words},
            )
        known = await self._tag_dict.active_values(user.tenant_id, added)
        unknown = [t for t in added if t not in known]
        if unknown:
            raise BloggerTagNotInDictError(
                f"这些标签不在标签字典里：{'、'.join(unknown)}；请先让主管在「标签字典」里添加",
                details={"tags": unknown},
            )


__all__ = ["BloggerService"]
