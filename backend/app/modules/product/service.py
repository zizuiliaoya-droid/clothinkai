"""U02 product 服务层（StyleService / SkuService）。

按 nfr-design/nfr-design-patterns.md 4 个 Pattern：
- P-U02-01：GIN trgm 模糊搜索（match）+ 降级语义
- P-U02-02：字段权限硬编码过渡（PRICE_VISIBLE_ROLES）
- P-U02-03：数据库原子 upsert（仅 SkuService.upsert_sku）
- P-U02-04：软删 + 引用检查（U02 占位 + U04/U16 扩展）

所有 ``# TODO U09`` 标记位置在 U09 阶段统一替换为 ``Permission.field_filter()``
/ ``Permission.field_writable()``，并删除 ``legacy_field_permissions.py``。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.audit import AuditService
from app.core.exceptions import ResourceNotFoundError
from app.core.metrics import sku_upsert_total, style_search_results_count
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.product.domain import (
    build_sku_audit_changes,
    build_style_audit_changes,
    compute_sku_changes,
    compute_style_changes,
    validate_sku_prices,
    validate_sku_sourcing_price,
)
from app.modules.product.exceptions import (
    FieldPermissionDenied,
    InvalidStyleReferenceError,
    SkuCodeConflictError,
    SkuHasReferenceError,
    SkuNotFoundError,
    StyleCodeConflictError,
    StyleHasActiveSkuError,
    StyleNotFoundError,
)
from app.modules.product.models import Sku, Style
from app.modules.product.repository import (
    StyleListFilters,
    StyleRepository,
    SkuRepository,
)
from app.modules.product.schemas import (
    CostTablePage,
    CostTableRow,
    MatchCandidate,
    MatchResponse,
    SkuCreate,
    SkuResponse,
    SkuUpdate,
    StyleCreate,
    StylePage,
    StyleResponse,
    StyleUpdate,
)


# ---------------------------------------------------------------------------
# StyleService
# ---------------------------------------------------------------------------


class StyleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._styles = StyleRepository(session)
        self._skus = SkuRepository(session)
        self._roles = RoleRepository(session)
        self._audit = AuditService(session)

    # ----------------------- create / update / delete ----------------------- #

    async def create_style(
        self, payload: StyleCreate, user: User
    ) -> StyleResponse:
        # BR-U02-01: 唯一性
        if await self._styles.code_exists(payload.style_code):
            raise StyleCodeConflictError(
                f"款式编码 {payload.style_code} 已被使用",
                details={"style_code": payload.style_code},
            )
        style = Style(
            style_code=payload.style_code,
            style_name=payload.style_name,
            short_name=payload.short_name,
            brand_id=payload.brand_id,
            category=payload.category.value,
            season=payload.season.value if payload.season else None,
            gender=payload.gender.value if payload.gender else None,
            tags=list(payload.tags),
            tag_color=list(payload.tag_color),
            main_image_key=payload.main_image_key,
            remark=payload.remark,
            owner_id=payload.owner_id,
            design_status=payload.design_status.value,
        )
        self._styles.add(style)
        await self._session.flush()

        # 审计：新建仅记录 style_code（敏感字段白名单）
        await self._audit.log(
            action="style.create",
            resource="style",
            resource_id=style.id,
            after={"style_code": style.style_code},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(style, user)

    async def update_style(
        self, style_id: UUID, payload: StyleUpdate, user: User
    ) -> StyleResponse:
        style = await self._styles.get_by_id(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")

        # BR-U02-01: 若改 style_code，需重新校验唯一
        if (
            "style_code" in payload.model_fields_set
            and payload.style_code is not None
            and payload.style_code != style.style_code
        ) and await self._styles.code_exists(payload.style_code):
            raise StyleCodeConflictError(
                f"款式编码 {payload.style_code} 已被使用",
                details={"style_code": payload.style_code},
            )

        changes = compute_style_changes(style, payload)
        if not changes:
            return await self._to_response(style, user)

        # 应用变更
        for field, diff in changes.items():
            new_value = getattr(payload, field)
            if field in {"category", "season", "gender", "design_status"}:
                # Enum → str 存储
                new_value = new_value.value if new_value is not None else None
            setattr(style, field, new_value)

        await self._session.flush()

        audit_changes = build_style_audit_changes(changes)
        if audit_changes:
            await self._audit.log(
                action="style.update",
                resource="style",
                resource_id=style.id,
                before={k: v["before"] for k, v in audit_changes.items()},
                after={k: v["after"] for k, v in audit_changes.items()},
                user_id=user.id,
            )
        await self._session.commit()
        return await self._to_response(style, user)

    async def soft_delete_style(self, style_id: UUID, user: User) -> None:
        """BR-U02-21: 删 style 前必须无启用 sku."""
        style = await self._styles.get_by_id(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")

        active_count = await self._skus.count_by_style(
            style_id, is_active=True, is_deleted=False
        )
        if active_count > 0:
            raise StyleHasActiveSkuError(
                f"款式下还有 {active_count} 个启用 SKU，请先停用或删除",
                details={"active_sku_count": active_count},
            )

        style.is_deleted = True
        style.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="style.delete",
            resource="style",
            resource_id=style.id,
            user_id=user.id,
        )
        await self._session.commit()

    async def disable_style(
        self, style_id: UUID, user: User
    ) -> StyleResponse:
        style = await self._styles.get_by_id(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")
        style.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="style.disable",
            resource="style",
            resource_id=style.id,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(style, user)

    async def restore_style(
        self, style_id: UUID, user: User
    ) -> StyleResponse:
        """BR-U02-22: 恢复软删的 style."""
        style = await self._styles.get_by_id(style_id, include_deleted=True)
        if style is None or not style.is_deleted:
            raise StyleNotFoundError(f"款式 {style_id} 不存在或未被软删")

        # 校验 style_code 是否已被新款占用
        existing = await self._styles.get_by_code(style.style_code)
        if existing is not None and existing.id != style.id:
            raise StyleCodeConflictError(
                f"款式编码 {style.style_code} 已被新款占用，请先重命名",
                details={"style_code": style.style_code},
            )

        style.is_deleted = False
        style.is_active = True
        await self._session.flush()
        await self._audit.log(
            action="style.restore",
            resource="style",
            resource_id=style.id,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(style, user)

    # ----------------------- read ----------------------- #

    async def get_style(self, style_id: UUID, user: User) -> StyleResponse:
        style = await self._styles.get_by_id(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")
        return await self._to_response(style, user)

    async def list_styles(
        self,
        *,
        filters: StyleListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> StylePage:
        items, total = await self._styles.list(
            filters=filters, page=page, page_size=page_size
        )
        return StylePage(
            items=[await self._to_response(s, user) for s in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ----------------------- match (BR-U02-50/51) ----------------------- #

    async def match_by_code(self, style_code: str) -> MatchResponse:
        """BR-U02-50: 精确反查款号."""
        style = await self._styles.get_by_code(style_code)
        if style is None or not style.is_active:
            return MatchResponse(matched=False, candidates=[], total=0)

        candidate = MatchCandidate(
            id=style.id,
            style_code=style.style_code,
            style_name=style.style_name,
            short_name=style.short_name,
            display_short_name=style.short_name or style.style_name,
        )
        return MatchResponse(matched=True, candidates=[candidate], total=1)

    async def match_by_keyword(self, keyword: str) -> MatchResponse:
        """BR-U02-51: 模糊反查（GIN trgm 索引 + similarity 排序）.

        注意降级语义（Pattern P-U02-01）：业务未匹配返回空候选 + 200；
        系统失败（DB 异常 / 超时）让异常自然冒泡 → 5xx + Sentry，
        **不要在这里 try/except 把系统错误转成空候选**。
        """
        if not keyword.strip():
            return MatchResponse(matched=False, candidates=[], total=0)

        rows = await self._styles.search_by_keyword(keyword.strip(), limit=20)
        candidates = [
            MatchCandidate(
                id=r.id,
                style_code=r.style_code,
                style_name=r.style_name,
                short_name=r.short_name,
                display_short_name=r.short_name or r.style_name,
            )
            for r in rows
        ]
        # 监控候选数分布（用于发现零候选率高的租户）
        style_search_results_count.observe(len(candidates))
        return MatchResponse(
            matched=bool(candidates),
            candidates=candidates,
            total=len(candidates),
        )

    # ----------------------- private ----------------------- #

    async def _to_response(self, style: Style, _user: User) -> StyleResponse:
        """Style 无字段级权限差异（cost_price 等不在 Style 表）."""
        main_url: str | None = None
        if style.main_image_key and attachment_service.is_configured:
            try:
                main_url = attachment_service.get_public_url(style.main_image_key)
            except Exception:  # noqa: BLE001
                main_url = None
        return StyleResponse(
            id=style.id,
            style_code=style.style_code,
            style_name=style.style_name,
            short_name=style.short_name,
            brand_id=style.brand_id,
            category=style.category,
            season=style.season,
            gender=style.gender,
            tags=list(style.tags or []),
            tag_color=list(style.tag_color or []),
            main_image_key=style.main_image_key,
            main_image_url=main_url,
            remark=style.remark,
            owner_id=style.owner_id,
            design_status=style.design_status,
            is_active=style.is_active,
            is_deleted=style.is_deleted,
            created_at=style.created_at,
            updated_at=style.updated_at,
        )


# ---------------------------------------------------------------------------
# SkuService
# ---------------------------------------------------------------------------


class SkuService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._skus = SkuRepository(session)
        self._styles = StyleRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)

    # ----------------------- create / update / delete ----------------------- #

    async def create_sku(self, payload: SkuCreate, user: User) -> SkuResponse:
        # BR-U02-12: 校验 style_id
        style = await self._styles.get_by_id(payload.style_id)
        if style is None:
            raise InvalidStyleReferenceError(
                "style_id 对应的款式不存在或已删除",
                details={"style_id": str(payload.style_id)},
            )
        # BR-U02-02: sku_code 唯一
        if await self._skus.code_exists(payload.sku_code):
            raise SkuCodeConflictError(
                f"SKU 编码 {payload.sku_code} 已被使用",
                details={"sku_code": payload.sku_code},
            )
        # BR-U02-13/14: 业务规则
        validate_sku_sourcing_price(payload, base=None)
        validate_sku_prices(payload)

        # BR-U02-41: 字段写权限（PRICE_VISIBLE_ROLES）
        await self._check_price_write_permission(payload, user)

        sku = Sku(
            style_id=payload.style_id,
            sku_code=payload.sku_code,
            color=payload.color,
            size=payload.size,
            cost_price=payload.cost_price,
            purchase_price=payload.purchase_price,
            base_price=payload.base_price,
            sourcing_type=payload.sourcing_type.value,
        )
        self._skus.add(sku)
        await self._session.flush()

        # 审计：新建仅记 sku_code 与 sourcing_type，敏感值脱敏
        after: dict[str, Any] = {
            "sku_code": sku.sku_code,
            "sourcing_type": sku.sourcing_type,
        }
        if sku.cost_price is not None:
            after["cost_price_changed"] = True
        if sku.purchase_price is not None:
            after["purchase_price_changed"] = True
        await self._audit.log(
            action="sku.create",
            resource="sku",
            resource_id=sku.id,
            after=after,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(sku, user)

    async def list_cost_table(
        self,
        *,
        keyword: str | None,
        brand_id: UUID | None,
        include_inactive: bool,
        page: int,
        page_size: int,
        user: User,
    ) -> "CostTablePage":
        """商品成本表（SKU 级，join 款式+品牌）。"""
        rows, total = await self._skus.list_cost_table(
            keyword=keyword,
            brand_id=brand_id,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
        items = [
            CostTableRow(
                sku_id=r.sku_id,
                style_id=r.style_id,
                image_key=r.main_image_key,
                style_code=r.style_code,
                sku_code=r.sku_code,
                style_name=r.style_name,
                short_name=r.short_name,
                color_size=f"{r.color} {r.size}".strip(),
                color=r.color,
                size=r.size,
                base_price=r.base_price,
                cost_price=r.cost_price,
                purchase_price=r.purchase_price,
                tag_price=r.tag_price,
                brand_name=r.brand_name,
                is_active=r.is_active,
            )
            for r in rows
        ]
        return CostTablePage(
            items=items, total=total, page=page, page_size=page_size
        )

    async def update_sku(
        self, sku_id: UUID, payload: SkuUpdate, user: User
    ) -> SkuResponse:
        sku = await self._skus.get_by_id(sku_id)
        if sku is None:
            raise SkuNotFoundError(f"SKU {sku_id} 不存在")

        # BR-U02-02: 改 sku_code 时唯一性
        if (
            "sku_code" in payload.model_fields_set
            and payload.sku_code is not None
            and payload.sku_code != sku.sku_code
        ) and await self._skus.code_exists(payload.sku_code):
            raise SkuCodeConflictError(
                f"SKU 编码 {payload.sku_code} 已被使用",
                details={"sku_code": payload.sku_code},
            )

        # BR-U02-13/14
        validate_sku_sourcing_price(payload, base=sku)
        validate_sku_prices(payload)

        # 字段写权限
        await self._check_price_write_permission(payload, user)

        changes = compute_sku_changes(sku, payload)
        if not changes:
            return await self._to_response(sku, user)

        # 应用变更
        for field in changes:
            new_value = getattr(payload, field)
            if field == "sourcing_type":
                new_value = new_value.value if new_value is not None else None
            setattr(sku, field, new_value)

        await self._session.flush()

        audit_changes = build_sku_audit_changes(changes)
        if audit_changes:
            # before/after 拆分
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for k, v in audit_changes.items():
                if isinstance(v, dict):
                    before[k] = v["before"]
                    after[k] = v["after"]
                else:
                    after[k] = v  # 脱敏 marker（cost_price_changed: true）
            await self._audit.log(
                action="sku.update",
                resource="sku",
                resource_id=sku.id,
                before=before or None,
                after=after,
                user_id=user.id,
            )
        await self._session.commit()
        return await self._to_response(sku, user)

    async def upsert_sku(
        self, payload: SkuCreate, user: User
    ) -> SkuResponse:
        """U06b 导入路径：数据库原子 upsert.

        Pattern P-U02-03：
        - ``ON CONFLICT (tenant_id, sku_code) WHERE is_deleted=false DO UPDATE``
        - 与 partial UNIQUE 严格对齐
        - 不"恢复"软删行（恢复走显式 endpoint，未在 U02 暴露）
        - audit 区分 ``sku.create_via_import`` / ``sku.update_via_import``
        - 必须复用同一套校验、权限、审计、敏感值脱敏

        不暴露 HTTP 端点；U06b 通过 ``from ... import SkuService`` 直接调用。
        """
        # BR-U02-12: 校验 style_id
        style = await self._styles.get_by_id(payload.style_id)
        if style is None:
            raise InvalidStyleReferenceError(
                "style_id 对应的款式不存在或已删除",
                details={"style_id": str(payload.style_id)},
            )
        # BR-U02-13/14
        validate_sku_sourcing_price(payload, base=None)
        validate_sku_prices(payload)
        # 字段写权限
        await self._check_price_write_permission(payload, user)

        values: dict[str, Any] = {
            "style_id": payload.style_id,
            "sku_code": payload.sku_code,
            "color": payload.color,
            "size": payload.size,
            "cost_price": payload.cost_price,
            "purchase_price": payload.purchase_price,
            "base_price": payload.base_price,
            "sourcing_type": payload.sourcing_type.value,
            "is_active": True,
            "is_deleted": False,
        }

        sku, is_inserted = await self._skus.upsert_atomic(
            tenant_id=user.tenant_id, values=values
        )

        # 指标：按 result 分类
        sku_upsert_total.labels(
            result="created" if is_inserted else "updated"
        ).inc()

        action = "sku.create_via_import" if is_inserted else "sku.update_via_import"
        after_marker: dict[str, Any] = {
            "sku_code": sku.sku_code,
            "sourcing_type": sku.sourcing_type,
        }
        if sku.cost_price is not None:
            after_marker["cost_price_changed"] = True
        if sku.purchase_price is not None:
            after_marker["purchase_price_changed"] = True
        await self._audit.log(
            action=action,
            resource="sku",
            resource_id=sku.id,
            after=after_marker,
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(sku, user)

    async def soft_delete_sku(self, sku_id: UUID, user: User) -> None:
        """BR-U02-20: 软删 + 引用检查."""
        sku = await self._skus.get_by_id(sku_id)
        if sku is None:
            raise SkuNotFoundError(f"SKU {sku_id} 不存在")

        refs = await self.check_references(sku_id)
        total_refs = sum(refs.values())
        if total_refs > 0:
            raise SkuHasReferenceError(
                f"该 SKU 已被引用（{refs}），仅可停用",
                details=refs,
            )

        sku.is_deleted = True
        sku.is_active = False
        await self._session.flush()
        await self._audit.log(
            action="sku.delete",
            resource="sku",
            resource_id=sku.id,
            user_id=user.id,
        )
        await self._session.commit()

    async def check_references(self, sku_id: UUID) -> dict[str, int]:
        """检查 sku 是否被其他模块引用。

        U02 阶段：promotion / order 表不存在，返回零引用。
        TODO U04: 改为 ``await self._promotion_repo.count_by_sku(sku_id)``
        TODO U16: 改为 ``await self._order_repo.count_by_sku(sku_id)``
        """
        # 依赖 sku_id 提示可能未来需要查询；保留参数命名以便 U04/U16 使用
        _ = sku_id
        return {"promotion_count": 0, "order_count": 0}

    # ----------------------- read ----------------------- #

    async def get_sku(self, sku_id: UUID, user: User) -> SkuResponse:
        sku = await self._skus.get_by_id(sku_id)
        if sku is None:
            raise SkuNotFoundError(f"SKU {sku_id} 不存在")
        return await self._to_response(sku, user)

    async def list_by_style(
        self, style_id: UUID, *, include_inactive: bool, user: User
    ) -> list[SkuResponse]:
        # 先校验 style 存在（跨租户由 RLS 自动过滤为 None）
        style = await self._styles.get_by_id(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")
        items = await self._skus.list_by_style(
            style_id, include_inactive=include_inactive
        )
        return [await self._to_response(s, user) for s in items]

    # ----------------------- private ----------------------- #

    async def _check_price_write_permission(
        self, payload: SkuCreate | SkuUpdate, user: User
    ) -> None:
        """BR-U02-41 / U09: 字段写权限（经 core 注册表 + 字段级 override）。"""
        # 仅当 payload 显式包含 cost_price / purchase_price 才校验
        fields_set = payload.model_fields_set
        sensitive_set = fields_set & {"cost_price", "purchase_price"}
        if not sensitive_set:
            return

        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        for offending in sensitive_set:
            if not can_write_field("sku", offending, ctx):
                raise FieldPermissionDenied(field=offending, entity="sku")

    async def _to_response(self, sku: Sku, user: User) -> SkuResponse:
        """BR-U02-41 / U09: 字段读过滤（经 core 注册表 + 字段级 override）。"""
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_cost = can_read_field("sku", "cost_price", ctx)
        can_see_purchase = can_read_field("sku", "purchase_price", ctx)

        return SkuResponse(
            id=sku.id,
            style_id=sku.style_id,
            sku_code=sku.sku_code,
            color=sku.color,
            size=sku.size,
            cost_price=sku.cost_price if can_see_cost else None,
            purchase_price=sku.purchase_price if can_see_purchase else None,
            base_price=sku.base_price,
            sourcing_type=sku.sourcing_type,
            is_active=sku.is_active,
            is_deleted=sku.is_deleted,
            created_at=sku.created_at,
            updated_at=sku.updated_at,
        )


__all__ = ["SkuService", "StyleService"]


# 抑制未使用 import（ResourceNotFoundError 给 IDE 提示用）
_ = ResourceNotFoundError
