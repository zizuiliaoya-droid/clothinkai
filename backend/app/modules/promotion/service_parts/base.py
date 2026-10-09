"""PromotionService 的共用部分：依赖注入、共用 helper、金额时间线、响应组装（从 service.py 搬出）。"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.audit import AuditService
from app.core.db import AsyncSessionBypass
from app.core.exceptions import ValidationError
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.core.tenancy import bypass_rls_ctx, request_id_ctx
from app.modules.auth.models import Tenant, User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.repository import BloggerRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.matrix import FlowActor, View, ui_for
from app.modules.product.models import Sku
from app.modules.product.repository import SkuRepository, StyleRepository
from app.modules.promotion.display_name import (
    normalize_goods_short_name,
    promotion_display_short_name,
)
from app.modules.promotion.domain import (
    compute_amount_changes,
)
from app.modules.promotion.enums import (
    AMOUNT_LOG_FIELDS,
    CooperationMode,
    RetroStatus,
)
from app.modules.promotion.exceptions import (
    FieldPermissionDenied,
    InvalidSkuReferenceError,
    PublishDateInFutureError,
    SourceExtraKeyRetiredError,
)
from app.modules.promotion.flow_doc import (
    PromotionDoc,
    build_promotion_doc,
    goods_items_complete,
)
from app.modules.promotion.legacy_settings import HIT_THRESHOLD_LIKE_COUNT
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
    calculate_is_hit,
)
from app.modules.promotion.models import (
    Promotion,
    PromotionAmountLog,
)
from app.modules.promotion.receiver import (
    RECEIVER_FIELDS,
    normalize_receiver_phone,
    retired_source_extra_keys,
)
from app.modules.promotion.repository import (
    PromotionAttachmentRefs,
    PromotionItemRepository,
    PromotionItemView,
    PromotionRepository,
)
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionAmountLogResponse,
    PromotionCreate,
    PromotionItemResponse,
    PromotionResponse,
    PromotionShipPushRequest,
    PromotionUpdate,
)
from app.modules.promotion.stage_calculator import compute_stage
from app.modules.promotion.urge_calculator import (
    UrgeThresholds,
    calculate_urge_status,
    get_today,
)
from app.modules.urge.enums import UrgeCloseReason
from app.modules.urge.service import UrgeService

# 沿用拆分前的 logger 名，日志检索不受影响
log = logging.getLogger("app.modules.promotion.service")

_PayloadT = TypeVar("_PayloadT", PromotionCreate, PromotionUpdate, PromotionShipPushRequest)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _assert_not_future_publish_date(actual: date | None) -> None:
    """实际发布日期不能晚于今天（7a-7，publish 与 resubmit 共用）。

    「今天」按 Asia/Shanghai（get_today），不用 date.today()——容器是 UTC，
    北京时间 0~8 点会把当天误判成明天。调用方要放在状态机判定之后（plan D4）。
    """
    if actual is None:
        return
    today = get_today()
    if actual > today:
        raise PublishDateInFutureError(
            f"实际发布日期不能晚于今天（{today.isoformat()}）",
            details={"actual_publish_date": actual.isoformat(), "today": today.isoformat()},
        )


class PromotionServiceBase:
    """PromotionService 各 mixin 共用的依赖与 helper。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PromotionRepository(session)
        self._items_repo = PromotionItemRepository(session)
        self._style_repo = StyleRepository(session)
        self._sku_repo = SkuRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)
        self._attachment_service = attachment_service

    # ============================================================
    # Private helpers
    # ============================================================

    async def _resolve_mode_costs(
        self,
        *,
        mode: CooperationMode,
        quote_amount: Decimal,
        goods_main_id: UUID | None,
        sku: Sku | None,
    ) -> tuple[Decimal, Decimal | None]:
        """按合作模式决定 (博主服务费, 样品成本)，强制覆盖调用方传值。

        PRD 模块二与第 9 节公式集：

        - 寄拍：衣服要寄回，样品不算成本 → ``cost_snapshot = 0``，只有寄回运费计入
        - 送拍 / 置换：衣服给了博主 → 样品成本 = 商品成员款式的货品成本之和
        - 置换：以货换推广 → ``quote_amount = 0``

        PRD 原文强调「强制覆盖，前端传值无效」，所以这里不是校验而是改写：
        前端传了寄拍 + 样品成本 500，照样落 0。

        送拍 / 置换取不到商品成本时回落到 SKU 成本价，再取不到就留空 —— 不编造 0，
        0 会让「这套不要钱」和「成本还没录」混在一起，报表上看不出区别。
        """
        if mode is CooperationMode.CONSIGNMENT:
            return quote_amount, Decimal("0")

        sample_cost: Decimal | None = None
        if goods_main_id is not None:
            sample_cost = await self._repo.sum_goods_sample_cost(goods_main_id)
        if sample_cost is None and sku is not None:
            sample_cost = sku.cost_price

        if mode is CooperationMode.BARTER:
            return Decimal("0"), sample_cost
        return quote_amount, sample_cost

    async def _signed_url_for(self, attachment_id: UUID | None) -> str | None:
        """给私有附件现签一个读 URL。签名失败不抛错 —— 一张图打不开不该让整条响应 500。"""
        if attachment_id is None:
            return None
        key = (
            await self._session.execute(
                sa_text("SELECT r2_key FROM attachment WHERE id = :aid AND status = 'ready'"),
                {"aid": attachment_id},
            )
        ).scalar_one_or_none()
        if not key:
            return None
        try:
            return self._attachment_service.get_signed_url("private", str(key), expires_in=900)
        except Exception:
            log.warning(
                "promotion_attachment_signed_url_failed",
                extra={"attachment_id": str(attachment_id)},
            )
            return None

    @staticmethod
    def _amount_snapshot(promotion: Promotion) -> dict[str, Decimal | None]:
        """取金额字段快照，给时间线算净变更用。"""
        return {f: getattr(promotion, f, None) for f in AMOUNT_LOG_FIELDS}

    def _log_amount_changes(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        before: dict[str, Decimal | None],
        after: dict[str, Decimal | None],
        payload: PromotionUpdate,
    ) -> None:
        """把金额净变更写进时间线。不 commit —— 跟着调用方的事务走。"""
        requested = {
            f: getattr(payload, f) for f in AMOUNT_LOG_FIELDS if f in payload.model_fields_set
        }
        for field, old, new, source in compute_amount_changes(
            before=before, after=after, requested=requested
        ):
            self._session.add(
                PromotionAmountLog(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    promotion_id=promotion_id,
                    field_name=field,
                    before_value=old,
                    after_value=new,
                    change_source=source,
                    changed_by=user_id,
                )
            )

    async def amount_log(
        self, promotion_id: UUID, user: User, *, limit: int = 100
    ) -> builtins.list[PromotionAmountLogResponse]:
        """金额变更时间线（PRD 第 10 节第 14 条）。

        **读权限走字段级判定，不靠 scope。** 新建一个 ``promotion.amount_log:read``
        挡不住运营 —— 他们持 ``promotion.*:read``，``has()`` 的前缀通配只看第一段，
        任何 ``promotion.xxx:read`` 都会被命中，于是能读到看不见的金额。
        这里用的是推广响应过滤金额时的同一个闸门。
        """
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_read_field("promotion", "quote_amount", ctx):
            raise FieldPermissionDenied(field="quote_amount", entity="promotion")

        rows = await self._repo.amount_log(
            tenant_id=user.tenant_id, promotion_id=promotion_id, limit=limit
        )
        return [PromotionAmountLogResponse(**r) for r in rows]

    async def _close_urge_task(
        self, promotion_id: UUID, user: User, reason: UrgeCloseReason
    ) -> None:
        """发布 / 取消时顺手关掉催发任务（PRD 改动 2）。

        在 service 内部局部 import：``urge.service`` 用到 ``promotion.urge_calculator``，
        模块级互引会绕回来。

        **故意吞掉异常**：没有催发任务、任务已关闭、甚至催发模块出问题，都不该让
        「发布推广单」这个主流程失败 —— 催发任务只是辅助视图。失败记一条 warning
        由自动扫描的 ``find_stale_open_tasks`` 兜底收口。
        """
        from app.modules.urge.service import UrgeService

        try:
            await UrgeService(self._session).close_for_promotion(
                promotion_id=promotion_id,
                tenant_id=user.tenant_id,
                reason=reason,
            )
        except Exception:
            log.warning(
                "urge_task_auto_close_failed",
                extra={"promotion_id": str(promotion_id), "reason": reason.value},
            )

    @staticmethod
    def _enforce_mode_costs(promotion: Promotion) -> None:
        """只强制两条不可协商的成本规则，其余保留当前值。

        和 ``_resolve_mode_costs`` 的区别：那个是建单时的**初始化**（会去汇总商品成员
        成本），这个是每次更新后的**兜底**。PRD 允许 PR 对送拍/置换的样品成本手动微调，
        所以这里绝对不能重算汇总值去覆盖人工录入 —— 只把两个恒等于 0 的字段压回 0。

        没有合作模式的历史单据不动：它们的成本口径本来就无从判断。
        """
        if promotion.cooperation_mode == CooperationMode.CONSIGNMENT.value:
            promotion.cost_snapshot = Decimal("0")
        elif promotion.cooperation_mode == CooperationMode.BARTER.value:
            promotion.quote_amount = Decimal("0")

    async def _check_amount_write_permission(
        self,
        payload: PromotionCreate | PromotionUpdate,
        user: User,
    ) -> None:
        """字段写权限校验（quote_amount）— U09 经 core 注册表 + 字段级 override。"""
        fields_set = payload.model_fields_set
        if "quote_amount" not in fields_set:
            return
        value = getattr(payload, "quote_amount", None)
        if value is None:
            # PATCH 显式传 None 等同于不修改业务上不要求权限
            return
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_write_field("promotion", "quote_amount", ctx):
            raise FieldPermissionDenied(field="quote_amount", entity="promotion")

    @staticmethod
    def _reject_retired_source_extra_keys(payload: PromotionCreate | PromotionUpdate) -> None:
        """``source_extra`` 带「打单地址」「发货单号」→ 422（流程线 7.1）。PATCH 与 ``POST /`` 最先判。"""
        keys = retired_source_extra_keys((payload.source_extra or {}).keys())
        if keys:
            raise SourceExtraKeyRetiredError(
                f"「{'」「'.join(keys)}」已改为收件 / 发货字段，不能再写进录入信息",
                details={"keys": keys},
            )

    async def _normalize_receiver(self, payload: _PayloadT, user: User) -> _PayloadT:
        """收件三项：字段写权限（越权的全列出来）→ 电话校验与规范化 → 去空白后空串 = 清空。

        只处理这次传了的（``model_fields_set``）；清空也算写，同样要写权限。
        返回带规范化值的副本，后面的比对、落库都用它。
        """
        fields = [f for f in RECEIVER_FIELDS if f in payload.model_fields_set]
        if not fields:
            return payload
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        denied = [f for f in fields if not can_write_field("promotion", f, ctx)]
        if denied:
            raise FieldPermissionDenied(fields=denied, entity="promotion")
        normalized: dict[str, str | None] = {}
        for field in fields:
            value: str | None = getattr(payload, field)
            if field == "receiver_phone":
                normalized[field] = normalize_receiver_phone(value)
            else:
                normalized[field] = (value.strip() or None) if value is not None else None
        return payload.model_copy(update=normalized)

    async def _validate_goods_items(
        self,
        *,
        goods_main_id: UUID | None,
        style_id: UUID,
        items: builtins.list[GoodsItemIn],
    ) -> builtins.list[tuple[UUID, UUID]]:
        """推广单商品明细入参校验（``POST /``；之后 ``PUT /{id}/items`` 与推送补选共用）。

        顺序：同一款式出现两次 → 款式集合 ≠ 归属商品的成员（多出 / 缺少都列出来）→ 有行没选 sku
        （都是 422 ``VALIDATION_ERROR``）→ sku 不存在、已删或不属于该行款式（422 ``INVALID_SKU_REFERENCE``）。
        返回按传入顺序的 ``(style_id, sku_id)``，给 ``PromotionItemRepository.replace``。
        """
        given = [item.style_id for item in items]
        duplicates = [s for s in dict.fromkeys(given) if given.count(s) > 1]
        if duplicates:
            raise ValidationError(
                "同一款式只能有一行颜色尺码",
                details={"duplicate_style_ids": [str(s) for s in duplicates]},
            )
        members = await self._items_repo.members_of(goods_main_id=goods_main_id, style_id=style_id)
        missing = [s for s in members if s not in given]
        extra = [s for s in given if s not in members]
        if missing or extra:
            raise ValidationError(
                "颜色尺码明细要与商品的成员款式一一对应",
                details={
                    "missing_style_ids": [str(s) for s in missing],
                    "extra_style_ids": [str(s) for s in extra],
                },
            )
        rows: builtins.list[tuple[UUID, UUID]] = []
        no_sku: builtins.list[UUID] = []
        for item in items:
            if item.sku_id is None:
                no_sku.append(item.style_id)
            else:
                rows.append((item.style_id, item.sku_id))
        if no_sku:
            raise ValidationError(
                "每个款式都要选颜色尺码",
                details={"missing_sku_style_ids": [str(s) for s in no_sku]},
            )
        skus = await self._items_repo.skus_by_ids([sku_id for _, sku_id in rows])
        for row_style_id, sku_id in rows:
            sku = skus.get(sku_id)
            if sku is None or sku.is_deleted or sku.style_id != row_style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {sku_id} 不存在、已删除或不属于款式 {row_style_id}",
                    details={"style_id": str(row_style_id), "sku_id": str(sku_id)},
                )
        return rows

    def _item_responses(
        self, views: builtins.list[PromotionItemView]
    ) -> builtins.list[PromotionItemResponse]:
        """明细行 → 响应。品名 = 款式简称（去首尾空格后为空回落款式名）；主图签名失败回 null。"""
        responses: builtins.list[PromotionItemResponse] = []
        for view in views:
            image_url: str | None = None
            if view.style_main_image_key:
                try:
                    image_url = self._attachment_service.get_signed_url(
                        "private", view.style_main_image_key, expires_in=3600
                    )
                except Exception:
                    log.warning(
                        "promotion_item_style_image_url_failed",
                        extra={"style_id": str(view.style_id)},
                    )
            responses.append(
                PromotionItemResponse(
                    style_id=view.style_id,
                    display_short_name=normalize_goods_short_name(view.style_short_name)
                    or view.style_name,
                    goods_title=view.style_name,
                    sku_id=view.sku_id,
                    color=view.color,
                    size=view.size,
                    style_main_image_url=image_url,
                )
            )
        return responses

    # ============================================================
    # 流程线矩阵（5.4）：当前用户、派生阶段、单据快照
    # ============================================================

    async def _flow_actor(self, user: User) -> FlowActor:
        return await load_flow_actor(user.id, self._roles, self._perms)

    async def _compute_stage(
        self,
        promotion: Promotion,
        *,
        today: date | None = None,
        thresholds: UrgeThresholds | None = None,
    ) -> str:
        """Python 版派生阶段（3.8；列表用 CTE 里的 SQL 版，两份由对拍测试守）。"""
        if thresholds is None:
            thresholds = await UrgeService(self._session).get_urge_thresholds(promotion.tenant_id)
        return compute_stage(
            publish_status=promotion.publish_status,
            recall_status=promotion.recall_status,
            settlement_status=promotion.settlement_status,
            ship_status=promotion.ship_status,
            is_active=promotion.is_active,
            scheduled_publish_date=promotion.scheduled_publish_date,
            today=today or get_today(),
            thresholds=thresholds,
        )

    async def _promotion_doc(
        self,
        promotion: Promotion,
        *,
        stage: str | None = None,
        items: builtins.list[PromotionItemView] | None = None,
        negotiators: Mapping[UUID, UUID] | None = None,
        members_by_goods: Mapping[UUID, builtins.list[UUID]] | None = None,
    ) -> PromotionDoc:
        """组矩阵快照。没透传的（阶段、明细、谈款人、套装成员）按这一张单现查。"""
        if stage is None:
            stage = await self._compute_stage(promotion)
        if items is None:
            items = (await self._items_repo.list_by_promotions([promotion.id])).get(
                promotion.id, []
            )
        if negotiators is None:
            negotiators = await self._repo.negotiator_ids([promotion.id])
        if members_by_goods is None:
            members = await self._items_repo.members_of(
                goods_main_id=promotion.goods_main_id, style_id=promotion.style_id
            )
        else:
            members = (
                members_by_goods.get(promotion.goods_main_id)
                if promotion.goods_main_id is not None
                else None
            ) or [promotion.style_id]
        return build_promotion_doc(
            promotion,
            stage=stage,
            negotiator_id=negotiators.get(promotion.id),
            items_complete=goods_items_complete((i.style_id for i in items), members),
        )

    async def _to_response(
        self,
        promotion: Promotion,
        user: User,
        *,
        today: Any = None,
        urge_status_override: str | None = None,
        dual_platform_override: bool | None = None,
        attachment_refs: PromotionAttachmentRefs | None = None,
        style_main_image_key: str | None = None,
        style_main_image_preloaded: bool = False,
        goods_code: str | None = None,
        goods_is_suit: bool | None = None,
        display_short_name: str | None = None,
        goods_title: str | None = None,
        goods_short_name: str | None = None,
        goods_preloaded: bool = False,
        items: builtins.list[PromotionItemView] | None = None,
        actor: FlowActor | None = None,
        view: View = "detail",
        stage: str | None = None,
        negotiators: Mapping[UUID, UUID] | None = None,
        members_by_goods: Mapping[UUID, builtins.list[UUID]] | None = None,
        user_names: Mapping[UUID, str] | None = None,
    ) -> PromotionResponse:
        """组装响应：字段权限过滤 + 衍生字段计算 + 矩阵 ``ui``.

        Args:
            urge_status_override: 列表查询时由 SQL CTE 计算后透传，避免重复计算。
            dual_platform_override: 同上。
            style_main_image_key: 列表查询预加载的款式主图 key。
            style_main_image_preloaded: 为 True 时不再查询 Style，避免列表 N+1。
            display_short_name / goods_title / goods_short_name: 列表 SQL 已算好的品名与
                归属商品名（``goods_preloaded`` 为 True 时用）；单条响应在这里按
                ``display_name.py`` 的同一规则现算。
            today: 列表查询时由 service 层 get_today() 透传，单条响应时缺省现算。
            items: 列表一页批量查好的商品明细（没有明细传 ``[]``）；None 时单条现查。
            actor: 当前用户的矩阵视角（列表整页共用一个）；None 时现查。字段权限上下文也取它的。
            view: ``ui`` 的形状，列表 ``list``（actions + edits），其余 ``detail``（actions + fields）。
            stage: 列表 CTE 算好的派生阶段；None 时按 ``stage_calculator.compute_stage`` 现算。
            negotiators / members_by_goods: 列表整页一次查好的谈款人与套装成员；None 时单条现查。
            user_names: 列表整页一次查好的推送人名字；None 时单条现查。
        """
        if actor is None:
            actor = await self._flow_actor(user)
        ctx = actor.field_ctx
        can_see_quote = can_read_field("promotion", "quote_amount", ctx)
        can_see_cost = can_read_field("promotion", "cost_snapshot", ctx)
        # 收件三项逐项过字段规则（个人授予 / 撤销可以只动其中一项）
        receiver: dict[str, Any] = {
            field: (getattr(promotion, field) if can_read_field("promotion", field, ctx) else None)
            for field in RECEIVER_FIELDS
        }
        can_see_payment_attachments = bool(
            ctx.role_codes & {"admin", "platform_admin", "pr", "pr_manager"}
        )

        payment_qr_url: str | None = None
        settlement_proof_url: str | None = None
        visible_payment_qr_id: UUID | None = None
        if can_see_payment_attachments:
            if attachment_refs is None:
                attachment_refs = (
                    await self._repo.get_payment_attachment_refs(
                        tenant_id=user.tenant_id, promotion_ids=[promotion.id]
                    )
                ).get(promotion.id)
            if attachment_refs is not None:
                visible_payment_qr_id = attachment_refs.payment_qr_attachment_id
                try:
                    if (
                        attachment_refs.payment_qr_status == "ready"
                        and attachment_refs.payment_qr_r2_key
                    ):
                        payment_qr_url = self._attachment_service.get_signed_url(
                            "private", attachment_refs.payment_qr_r2_key, expires_in=900
                        )
                    if (
                        attachment_refs.settlement_proof_status == "ready"
                        and attachment_refs.settlement_proof_r2_key
                    ):
                        settlement_proof_url = self._attachment_service.get_signed_url(
                            "private", attachment_refs.settlement_proof_r2_key, expires_in=900
                        )
                except Exception:
                    log.warning("promotion_payment_attachment_signed_url_failed")

        resolved_style_image_key = style_main_image_key
        if not style_main_image_preloaded:
            style = await self._style_repo.get_by_id(promotion.style_id)
            resolved_style_image_key = style.main_image_key if style is not None else None

        # 商品归属实时取（不做快照，因为归属可改）。列表查询已 JOIN 出来，避免 N+1。
        resolved_goods_code = goods_code
        resolved_goods_is_suit = bool(goods_is_suit)
        resolved_goods_title = goods_title
        resolved_goods_short_name = goods_short_name
        resolved_display_short_name = display_short_name
        if not goods_preloaded:
            # 品名规则与列表 SQL 同一份（display_name.py）：商品简称，没填回落快照
            raw_short_name: str | None = None
            if promotion.goods_main_id is not None:
                goods_row = (
                    await self._session.execute(
                        sa_text(
                            "SELECT goods_code, is_suit, short_name, goods_title "
                            "FROM goods_main WHERE id = :gid"
                        ),
                        {"gid": promotion.goods_main_id},
                    )
                ).one_or_none()
                if goods_row is not None:
                    resolved_goods_code, resolved_goods_is_suit = (
                        goods_row[0],
                        bool(goods_row[1]),
                    )
                    raw_short_name, resolved_goods_title = goods_row[2], goods_row[3]
            resolved_goods_short_name = normalize_goods_short_name(raw_short_name)
            resolved_display_short_name = promotion_display_short_name(
                goods_short_name=raw_short_name,
                style_short_name_snapshot=promotion.style_short_name_snapshot,
            )
        style_main_image_url: str | None = None
        if resolved_style_image_key:
            try:
                style_main_image_url = self._attachment_service.get_signed_url(
                    "private", resolved_style_image_key, expires_in=3600
                )
            except Exception:
                log.warning(
                    "promotion_style_main_image_url_failed",
                    extra={"promotion_id": str(promotion.id)},
                )

        # 衍生字段计算
        if today is None:
            today = get_today()
        thresholds: UrgeThresholds | None = None
        if urge_status_override is not None:
            urge_status = urge_status_override
        else:
            # 单条响应（详情、各状态推进）才走到这里；列表由 SQL 算好透传进来
            thresholds = await UrgeService(self._session).get_urge_thresholds(promotion.tenant_id)
            urge_status = calculate_urge_status(
                publish_status=promotion.publish_status,
                scheduled_publish_date=promotion.scheduled_publish_date,
                today=today,
                urge_threshold_days=thresholds.urge_days,
                important_threshold_days=thresholds.important_days,
            )
        if dual_platform_override is None:
            dual_platform = await self._repo.has_other_platforms_for_style(
                style_id=promotion.style_id,
                platform=promotion.platform,
                exclude_id=promotion.id,
            )
        else:
            dual_platform = dual_platform_override

        effective_like = calculate_effective_like_count(
            platform=promotion.platform, like_count=promotion.like_count
        )
        is_hit = calculate_is_hit(
            like_count=promotion.like_count, threshold=HIT_THRESHOLD_LIKE_COUNT
        )
        cpl = calculate_cpl(
            total_promo_cost=promotion.total_promo_cost,
            effective_like_count=effective_like,
            metrics_recorded_at=promotion.metrics_recorded_at,
        )

        # 7 天数据截图与当前复盘文字（PRD V1.4 改动 4）。
        # 两项都只在单据真的进了复盘流程后才查，没进的单（生产上绝大多数）不多付代价。
        metrics_url = await self._signed_url_for(promotion.metrics_attachment_id)
        brand_comment_url = await self._signed_url_for(promotion.brand_comment_attachment_id)
        retro_content: str | None = None
        if promotion.retro_status != RetroStatus.NOT_STARTED.value:
            retro = await self._repo.latest_retrospective(
                tenant_id=promotion.tenant_id, promotion_id=promotion.id
            )
            retro_content = retro.content if retro is not None else None

        # 商品明细；没有明细的旧单回落「录入信息」里的颜色及规格原文（2.2：旧单留档）
        if items is None:
            items = (await self._items_repo.list_by_promotions([promotion.id])).get(
                promotion.id, []
            )
        source_extra = dict(getattr(promotion, "source_extra", {}) or {})
        legacy_spec = source_extra.get("颜色及规格")
        legacy_color_spec = None if items or legacy_spec is None else str(legacy_spec)

        # 矩阵 ui（7.1）：列表的阶段、谈款人、套装成员整页一次查好透传，单条现算
        if stage is None:
            stage = await self._compute_stage(promotion, today=today, thresholds=thresholds)
        doc = await self._promotion_doc(
            promotion,
            stage=stage,
            items=items,
            negotiators=negotiators,
            members_by_goods=members_by_goods,
        )
        ui = ui_for(actor, doc).to_dict(view=view)

        pushed_by = promotion.ship_pushed_by
        if pushed_by is not None and user_names is None:
            user_names = await self._repo.user_names([pushed_by])

        return PromotionResponse(
            id=promotion.id,
            internal_code=promotion.internal_code,
            style_id=promotion.style_id,
            sku_id=promotion.sku_id,
            goods_main_id=promotion.goods_main_id,
            blogger_id=promotion.blogger_id,
            pr_id=promotion.pr_id,
            style_code_snapshot=promotion.style_code_snapshot,
            style_short_name_snapshot=promotion.style_short_name_snapshot,
            display_short_name=resolved_display_short_name,
            style_main_image_url=style_main_image_url,
            goods_code=resolved_goods_code,
            goods_is_suit=resolved_goods_is_suit,
            goods_title=resolved_goods_title,
            goods_short_name=resolved_goods_short_name,
            quote_amount=(promotion.quote_amount if can_see_quote else None),
            cost_snapshot=(promotion.cost_snapshot if can_see_cost else None),
            cooperation_mode=promotion.cooperation_mode,
            return_shipping_fee=(promotion.return_shipping_fee if can_see_cost else None),
            # 站外推广成本是三项金额之和，能看到它等于能推算金额，所以两个读权限都要有
            total_promo_cost=(
                promotion.total_promo_cost if (can_see_quote and can_see_cost) else None
            ),
            return_waybill=promotion.return_waybill,
            platform=promotion.platform,
            cooperation_date=promotion.cooperation_date,
            scheduled_publish_date=promotion.scheduled_publish_date,
            actual_publish_date=promotion.actual_publish_date,
            publish_url=promotion.publish_url,
            cancel_reason=promotion.cancel_reason,
            recall_reason=promotion.recall_reason,
            like_count=promotion.like_count,
            collect_count=promotion.collect_count,
            comment_count=promotion.comment_count,
            metrics_recorded_at=promotion.metrics_recorded_at,
            metrics_signed_url=metrics_url,
            brand_comment_attachment_id=promotion.brand_comment_attachment_id,
            brand_comment_signed_url=brand_comment_url,
            note_title=promotion.note_title,
            remark=promotion.remark,
            publish_status=promotion.publish_status,
            recall_status=promotion.recall_status,
            settlement_status=promotion.settlement_status,
            retro_status=promotion.retro_status,
            retro_confirmed_by=promotion.retro_confirmed_by,
            retro_confirmed_at=promotion.retro_confirmed_at,
            retro_content=retro_content,
            reviewed_by=promotion.reviewed_by,
            reviewed_at=promotion.reviewed_at,
            review_action=promotion.review_action,
            review_reason=promotion.review_reason,
            review_reason_category=promotion.review_reason_category,
            resubmit_note=promotion.resubmit_note,
            resubmitted_at=promotion.resubmitted_at,
            is_active=promotion.is_active,
            created_at=promotion.created_at,
            updated_at=promotion.updated_at,
            urge_status=urge_status,
            dual_platform=dual_platform,
            effective_like_count=effective_like,
            is_hit=is_hit,
            # 分子是三项金额之和，单赞成本 × 点赞数就能反推出来 —— 与 total_promo_cost 同样
            # 要两个读权限都有
            cpl=cpl if (can_see_quote and can_see_cost) else None,
            source_extra=source_extra,
            **receiver,
            ship_status=promotion.ship_status,
            ship_pushed_at=promotion.ship_pushed_at,
            ship_pushed_by_name=(
                user_names.get(pushed_by) if pushed_by is not None and user_names else None
            ),
            ship_courier=promotion.ship_courier,
            ship_waybill=promotion.ship_waybill,
            shipped_at=promotion.shipped_at,
            items=self._item_responses(items),
            legacy_color_spec=legacy_color_spec,
            payment_qr_attachment_id=visible_payment_qr_id,
            payment_qr_signed_url=payment_qr_url,
            settlement_payment_proof_signed_url=settlement_proof_url,
            duplicate_warnings=[],
            ui=ui,
        )

    async def _log_event_dispatch_failure(
        self,
        event: Any,
        exc: Exception,
        user: User,
        *,
        blocking: bool,
    ) -> None:
        """事件分发失败的独立 audit（FB5 脱敏 + 兜底）.

        严格脱敏 — 不写 ``str(exc)`` / SQL / 金额 / 内部路径。
        audit 自身写入失败不能覆盖原异常。

        Args:
            blocking: True = 强一致事件（SettlementRequested），调用方会重新 raise；
                      False = 通知类事件（PromotionPublished），不阻塞主流程。
        """
        safe_payload = {
            "event_type": getattr(event, "event_type", "unknown"),
            "event_id": str(getattr(event, "event_id", "")),
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "promotion_id": str(getattr(event, "promotion_id", "") or ""),
            "request_id": request_id_ctx.get() or None,
            "blocking": blocking,
        }

        # 用独立 bypass session 写 audit，避免被原事务回滚带走
        token = bypass_rls_ctx.set(True)
        try:
            try:
                async with AsyncSessionBypass() as audit_session:
                    audit_service = AuditService(audit_session)
                    await audit_service.log(
                        action="promotion.event_dispatch_failed",
                        resource="promotion",
                        resource_id=getattr(event, "promotion_id", None),
                        after=safe_payload,
                        actor_type="system",
                        user_id=user.id,
                    )
                    await audit_session.commit()
            except Exception as audit_exc:
                # 兜底：audit 写失败仅 log，不覆盖原异常
                log.exception(
                    "audit_for_event_failure_itself_failed",
                    extra={
                        "original_error": type(exc).__name__,
                        "audit_error": type(audit_exc).__name__,
                    },
                )
                # 不重新抛 audit_exc，让原 exc 继续上抛
        finally:
            bypass_rls_ctx.reset(token)

    async def _get_tenant_code(self, tenant_id: UUID) -> str:
        """取 tenant.code 用于 internal_code 前缀."""
        result = await self._session.execute(select(Tenant.code).where(Tenant.id == tenant_id))
        code = result.scalar_one_or_none()
        return str(code or "")
