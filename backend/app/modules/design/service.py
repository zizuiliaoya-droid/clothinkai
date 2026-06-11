"""U10a DesignService（设计制版业务编排）。

P-U10a-01：状态机语义校验（不 setattr）+ repository 乐观并发推进 + 副作用同事务。
P-U10a-02：自动核价系统口径写 SKU（绕过 U09 字段写校验）+ audit 脱敏。
P-U10a-03：driven_by / 通知角色服务端推断（防伪）+ notify 同事务。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import (
    IllegalStateTransitionError,
    PermissionDeniedError,
    ValidationError,
)
from app.modules.auth.models import User
from app.modules.auth.repository import RoleRepository
from app.modules.design.domain import compute_available_actions, compute_total_cost
from app.modules.design.enums import (
    DRIVEN_BY,
    NOTIFY_ROLE,
    REJECT_PREVIOUS,
    TERMINAL_STATUSES,
    DesignStatus as DS,
)
from app.modules.design.exceptions import (
    CancelReasonRequiredError,
    DesignStateConflictError,
    RejectReasonRequiredError,
    StyleNotFoundError,
)
from app.modules.design.repository import DesignRepository
from app.modules.design.schemas import (
    CostingSubmit,
    CraftSubmit,
    DesignCreate,
    DesignDetailResponse,
    DesignListItem,
    DesignListResponse,
    DesignStatusGroup,
    FabricComplete,
    FabricSubmit,
    GradingSubmit,
    PatternSubmit,
    TagPriceSubmit,
    WorkflowLogEntry,
)
from app.modules.design.state_machines import make_design_state_machine
from app.modules.product.models import Style
from app.modules.wecom.enums import NotificationType
from app.modules.wecom.notification_service import NotificationService

_NOTIFY_TYPE = {
    "submit_fabric": NotificationType.DESIGN_ADVANCE,
    "submit_grading": NotificationType.DESIGN_ADVANCE,
    "submit_craft": NotificationType.DESIGN_ADVANCE,
    "submit_costing": NotificationType.DESIGN_ADVANCE,
    "confirm_price": NotificationType.DESIGN_DONE,
}


class DesignService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = DesignRepository(session)
        self._roles = RoleRepository(session)
        self._notifier = NotificationService(session)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    async def _require_style(self, style_id: UUID) -> Style:
        style = await self._repo.get_style(style_id)
        if style is None:
            raise StyleNotFoundError(f"款式 {style_id} 不存在")
        return style

    def _assert_status(self, style: Style, expected: str) -> None:
        if style.design_status != expected:
            raise IllegalStateTransitionError(
                f"当前状态 {style.design_status} 不允许此操作",
                details={"current_state": style.design_status, "expected": expected},
            )

    def _validate_rule(self, style: Style, action: str, roles: list[str], payload: dict):
        """状态机语义校验（不 setattr，避免 autoflush 抢先改 design_status）。"""
        sm = make_design_state_machine(style)
        rule = sm._find_rule(action)
        if rule is None:
            raise IllegalStateTransitionError(
                f"状态 {style.design_status} 不允许动作 {action}",
                details={"current_state": style.design_status, "action": action},
            )
        if rule.actor_roles and not any(r in rule.actor_roles for r in roles):
            raise PermissionDeniedError(
                f"角色 {roles} 不允许执行 {action}",
                details={"required_roles": list(rule.actor_roles)},
            )
        if rule.required_fields:
            missing = [f for f in rule.required_fields if not payload.get(f)]
            if missing:
                raise ValidationError(
                    f"动作 {action} 缺失必填字段: {missing}",
                    details={"missing_fields": missing},
                )
        return rule

    async def _advance(self, style: Style, action: str, roles: list[str], payload: dict, user: User):
        rule = self._validate_rule(style, action, roles, payload)
        ok = await self._repo.update_design_status(
            style.id, rule.from_state, rule.to_state
        )
        if not ok:
            raise DesignStateConflictError()
        style.design_status = rule.to_state  # 同步内存对象（已守卫推进成功）
        self._repo.add_workflow_log(
            style.id, rule.from_state, rule.to_state, action=action, actor_id=user.id
        )
        return rule

    async def _notify_role(self, action: str, style: Style, *, reason: str | None = None) -> None:
        role = NOTIFY_ROLE.get(action)
        if role is None:
            return
        user_ids = await self._roles.list_user_ids_by_role_code(role)
        if not user_ids:
            return
        content = f"款式 {style.style_code} 进入「{style.design_status}」环节"
        if reason:
            content += f"（原因：{reason}）"
        await self._notifier.notify(
            user_ids,
            content,
            link=f"/designs/{style.id}",
            type=_NOTIFY_TYPE.get(action, NotificationType.DESIGN_ADVANCE).value,
        )

    # ------------------------------------------------------------------ #
    # UC-1 create_design（S02）
    # ------------------------------------------------------------------ #
    async def create_design(self, payload: DesignCreate, user: User) -> DesignDetailResponse:
        if await self._repo.style_code_exists(payload.style_code):
            from app.modules.product.exceptions import StyleCodeConflictError

            raise StyleCodeConflictError(
                f"款式编码 {payload.style_code} 已被使用",
                details={"style_code": payload.style_code},
            )
        style = Style(
            style_code=payload.style_code,
            style_name=payload.style_name,
            short_name=payload.short_name,
            category=payload.category,
            main_image_key=payload.main_image_key,
            owner_id=user.id,
            design_status=DS.DESIGNING.value,
        )
        self._repo.add_style(style)
        await self._session.flush()
        self._repo.add_workflow_log(
            style.id, None, DS.DESIGNING.value, action="create", actor_id=user.id
        )
        await self._audit.log(
            action="design.create", resource="style", resource_id=style.id,
            after={"style_code": style.style_code}, user_id=user.id,
        )
        await self._session.commit()
        return await self.get_detail(style.id, user)

    # ------------------------------------------------------------------ #
    # 推进动作（advance + side effects）
    # ------------------------------------------------------------------ #
    async def submit_fabric(self, style_id: UUID, payload: FabricSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        await self._repo.upsert_fabric(
            style_id, fabrics=payload.fabrics, accessories=payload.accessories,
            remark=payload.remark,
        )
        await self._advance(style, "submit_fabric", roles, {"fabrics": payload.fabrics}, user)
        await self._notify_role("submit_fabric", style)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def submit_grading(self, style_id: UUID, payload: GradingSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        pattern = await self._repo.get_pattern(style_id)
        if pattern is None:
            raise ValidationError("版型尚未上传，不能放码", details={"style_id": str(style_id)})
        await self._repo.upsert_pattern(style_id, grading_data=payload.grading_data)
        await self._advance(style, "submit_grading", roles, {}, user)
        await self._notify_role("submit_grading", style)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def submit_craft(self, style_id: UUID, payload: CraftSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        await self._repo.upsert_craft(style_id, craft_info=payload.craft_info)
        await self._advance(style, "submit_craft", roles, {"craft_info": payload.craft_info}, user)
        await self._notify_role("submit_craft", style)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def submit_costing(self, style_id: UUID, payload: CostingSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        cb = payload.cost_breakdown
        total = compute_total_cost(cb.fabric_cost, cb.accessory_cost, cb.craft_cost)
        n = await self._repo.bulk_update_sku_cost_price(style_id, total)  # 系统口径绕过 U09
        await self._advance(style, "submit_costing", roles, {"cost_breakdown": cb.model_dump()}, user)
        await self._audit.log(
            action="design.auto_costing", resource="style", resource_id=style_id,
            after={"cost_price_changed": True, "sku_count": n}, user_id=user.id,
        )
        await self._notify_role("submit_costing", style)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def confirm_price(self, style_id: UUID, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        await self._advance(style, "confirm_price", roles, {}, user)
        await self._notify_role("confirm_price", style)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    # ------------------------------------------------------------------ #
    # 原地动作（无状态推进，不通知）
    # ------------------------------------------------------------------ #
    async def submit_pattern(self, style_id: UUID, payload: PatternSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        self._assert_status(style, DS.PATTERNING.value)
        await self._repo.upsert_pattern(
            style_id, pattern_no=payload.pattern_no, pattern_file_key=payload.pattern_file_key
        )
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def complete_fabric(self, style_id: UUID, payload: FabricComplete, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        self._assert_status(style, DS.COMPLETING.value)
        await self._repo.upsert_fabric(
            style_id, fabrics=payload.fabrics, accessories=payload.accessories,
            remark=payload.remark, is_completed=True,
        )
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def set_tag_price(self, style_id: UUID, payload: TagPriceSubmit, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        self._assert_status(style, DS.PRICING.value)
        await self._repo.bulk_update_sku_tag_price(style_id, payload.tag_price)
        await self._session.commit()
        return await self.get_detail(style_id, user)

    # ------------------------------------------------------------------ #
    # reject / cancel（动态目标）
    # ------------------------------------------------------------------ #
    async def reject(self, style_id: UUID, reason: str, user: User) -> DesignDetailResponse:
        if not reason or not reason.strip():
            raise RejectReasonRequiredError("驳回必须填写原因")
        style = await self._require_style(style_id)
        cur = style.design_status
        prev = REJECT_PREVIOUS.get(cur)
        if prev is None:
            raise IllegalStateTransitionError(
                f"状态 {cur} 不可驳回", details={"current_state": cur}
            )
        ok = await self._repo.update_design_status(style_id, cur, prev)
        if not ok:
            raise DesignStateConflictError()
        style.design_status = prev
        self._repo.add_workflow_log(
            style_id, cur, prev, action="reject",
            driven_by=DRIVEN_BY.get(cur), actor_id=user.id, reason=reason,
        )
        await self._audit.log(
            action="design.reject", resource="style", resource_id=style_id,
            after={"from": cur, "to": prev, "reason_provided": True}, user_id=user.id,
        )
        # 通知上游（回退后 style.design_status=prev → 通知该环节负责角色）
        upstream = {
            DS.DESIGNING.value: "designer", DS.PATTERNING.value: "pattern_maker",
            DS.CRAFTING.value: "merchandiser", DS.COMPLETING.value: "design_assistant",
        }.get(prev)
        if upstream:
            user_ids = await self._roles.list_user_ids_by_role_code(upstream)
            if user_ids:
                await self._notifier.notify(
                    user_ids, f"款式 {style.style_code} 被驳回（原因：{reason}）",
                    link=f"/designs/{style_id}", type=NotificationType.DESIGN_REJECT.value,
                )
        await self._session.commit()
        return await self.get_detail(style_id, user)

    async def cancel(self, style_id: UUID, reason: str, user: User) -> DesignDetailResponse:
        if not reason or not reason.strip():
            raise CancelReasonRequiredError("取消必须填写原因")
        roles = await self._roles.list_codes_for_user(user.id)
        if "admin" not in roles and "platform_admin" not in roles:
            raise PermissionDeniedError("仅管理员可取消款式")
        style = await self._require_style(style_id)
        cur = style.design_status
        if cur in TERMINAL_STATUSES:
            raise IllegalStateTransitionError(
                f"终态 {cur} 不可取消", details={"current_state": cur}
            )
        ok = await self._repo.update_design_status(style_id, cur, DS.CANCELLED.value)
        if not ok:
            raise DesignStateConflictError()
        style.design_status = DS.CANCELLED.value
        self._repo.add_workflow_log(
            style_id, cur, DS.CANCELLED.value, action="cancel",
            driven_by="admin", actor_id=user.id, reason=reason,
        )
        await self._audit.log(
            action="design.cancel", resource="style", resource_id=style_id,
            after={"from": cur, "reason_provided": True}, user_id=user.id,
        )
        await self._session.commit()
        return await self.get_detail(style_id, user)

    # ------------------------------------------------------------------ #
    # 读：list / detail
    # ------------------------------------------------------------------ #
    async def list_designs(self, user: User) -> DesignListResponse:
        counts = await self._repo.list_grouped(tenant_id=user.tenant_id)
        count_map = dict(counts)
        groups: list[DesignStatusGroup] = []
        total = 0
        for status in (
            DS.DESIGNING, DS.PATTERNING, DS.CRAFTING, DS.COMPLETING,
            DS.PRICING, DS.MASS_PRODUCTION, DS.CANCELLED,
        ):
            cnt = count_map.get(status.value, 0)
            total += cnt
            items = await self._repo.list_by_status(
                tenant_id=user.tenant_id, design_status=status.value, limit=50
            )
            groups.append(
                DesignStatusGroup(
                    status=status.value,
                    count=cnt,
                    items=[
                        DesignListItem(
                            id=s.id, style_code=s.style_code, style_name=s.style_name,
                            design_status=s.design_status, main_image_key=s.main_image_key,
                        )
                        for s in items
                    ],
                )
            )
        return DesignListResponse(groups=groups, total=total)

    async def get_detail(self, style_id: UUID, user: User) -> DesignDetailResponse:
        style = await self._require_style(style_id)
        fabric = await self._repo.get_fabric(style_id)
        pattern = await self._repo.get_pattern(style_id)
        craft = await self._repo.get_craft(style_id)
        logs = await self._repo.list_workflow_log(style_id)
        roles = await self._roles.list_codes_for_user(user.id)
        return DesignDetailResponse(
            id=style.id,
            style_code=style.style_code,
            style_name=style.style_name,
            design_status=style.design_status,
            main_image_key=style.main_image_key,
            fabric=(
                {"fabrics": fabric.fabrics, "accessories": fabric.accessories,
                 "is_completed": fabric.is_completed, "remark": fabric.remark}
                if fabric else None
            ),
            pattern=(
                {"pattern_no": pattern.pattern_no, "pattern_file_key": pattern.pattern_file_key,
                 "grading_data": pattern.grading_data}
                if pattern else None
            ),
            craft={"craft_info": craft.craft_info} if craft else None,
            workflow_log=[WorkflowLogEntry.model_validate(log) for log in logs],
            available_actions=compute_available_actions(style.design_status, roles),
        )


__all__ = ["DesignService"]
