"""U04 promotion 服务层（PromotionService）。

按 nfr-design/logical-components.md §4.1 + nfr-design-patterns.md §2-§5：

业务编排层：
- CRUD：create_promotion / update_promotion / get_promotion / list_promotions / soft_delete_promotion
- 状态推进（6 个）：publish / cancel / start_recall / recall_success / recall_failure / review
- 内部 API：update_like_count（U13 数据采集 Worker 调用）

关键设计：
- 状态机推进通过 ``repository.update_state``（FB7：UPDATE WHERE old_state RETURNING）
- review approve 同事务发 SettlementRequested 事件（FB1：required_handler）
- 失败 audit 脱敏 + 兜底（FB5）
- 字段写权限硬编码（待 U09 清理）
- 衍生字段实时计算（urge_status / dual_platform / effective_like_count / is_hit / cpl）
- match 降级语义：业务未匹配 → 200 + 空数组；系统失败 → 异常自然冒泡 → 5xx + Sentry

按职责拆成 mixin（``service_parts/``）：本文件留 CRUD 与仓库回填；共用依赖与 helper 在 ``base.py``，
收款码 / 发布与取消 / 召回 / 审核与重提 / 数据与复盘各一个文件。``PromotionService`` 的名字与路径不变。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.metrics import (
    promotion_search_results_count,
)
from app.modules.auth.models import User
from app.modules.product.models import Sku
from app.modules.promotion.domain import (
    build_promotion_audit_changes,
    compute_promotion_changes,
    format_internal_code,
    merge_source_extra,
)
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    SettlementStatus,
)
from app.modules.promotion.exceptions import (
    CooperationModeImmutableError,
    InvalidBloggerReferenceError,
    InvalidGoodsReferenceError,
    InvalidSkuReferenceError,
    InvalidStyleReferenceError,
    PromotionNotFoundError,
    PublishUrlRequiredError,
    StateTransitionConflictError,
)
from app.modules.promotion.legacy_settings import HIT_THRESHOLD_LIKE_COUNT
from app.modules.promotion.models import (
    Promotion,
)
from app.modules.promotion.repository import (
    PromotionListFilters as RepoPromotionListFilters,
)
from app.modules.promotion.schemas import (
    PromotionCreate,
    PromotionDuplicateWarning,
    PromotionPage,
    PromotionResponse,
    PromotionUpdate,
    PromotionWarehouseWaybillRequest,
)
from app.modules.promotion.schemas import (
    PromotionListFilters as ApiPromotionListFilters,
)

# 拆分前这三个是本模块的模块级名字，保留可从这里 import
from app.modules.promotion.service_parts.base import (
    _assert_not_future_publish_date,  # noqa: F401
    _utcnow,  # noqa: F401
    log,  # noqa: F401
)
from app.modules.promotion.service_parts.payment_qr import PromotionPaymentQrMixin
from app.modules.promotion.service_parts.publish import PromotionPublishMixin
from app.modules.promotion.service_parts.recall import PromotionRecallMixin
from app.modules.promotion.service_parts.retro import PromotionRetroMixin
from app.modules.promotion.service_parts.review import PromotionReviewMixin
from app.modules.promotion.urge_calculator import (
    get_today,
)
from app.modules.urge.service import UrgeService


class PromotionService(
    PromotionPaymentQrMixin,
    PromotionPublishMixin,
    PromotionRecallMixin,
    PromotionReviewMixin,
    PromotionRetroMixin,
):
    """推广合作业务服务。"""

    # ============================================================
    # CRUD: create
    # ============================================================

    async def create_promotion(
        self, payload: PromotionCreate, user: User, *, autocommit: bool = True
    ) -> PromotionResponse:
        """EP05-S02 创建推广 + 自动 internal_code + 重复检测.

        Args:
            autocommit: 默认 True，HTTP 路径下自己提交。谈款审核通过时要在同一个事务里
                「改谈款状态 + 建推广单」，那边传 False 由调用方统一提交 ——
                否则中间失败会留下「审核通过但没有推广单」的单据。
        """
        # 0. 退役键最先判（流程线 7.3）
        self._reject_retired_source_extra_keys(payload)

        # 1. 引用完整性
        style = await self._style_repo.get_by_id(payload.style_id)
        if style is None:
            raise InvalidStyleReferenceError(f"款式 {payload.style_id} 不存在或已删除")

        sku: Sku | None = None
        if payload.sku_id is not None:
            sku = await self._sku_repo.get_by_id(payload.sku_id)
            if sku is None:
                raise InvalidSkuReferenceError(f"SKU {payload.sku_id} 不存在或已删除")
            if sku.style_id != payload.style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {payload.sku_id} 不属于款式 {payload.style_id}",
                    details={
                        "sku_style_id": str(sku.style_id),
                        "expected_style_id": str(payload.style_id),
                    },
                )

        # 商品归属：传了就校验商品确实包含该款式；没传就推定主商品（非套装优先），
        # 与报表兜底同序，保证旧客户端与 Excel 导入的行为不变。
        goods_main_id = payload.goods_main_id
        if goods_main_id is not None:
            if not await self._repo.goods_contains_style(
                goods_main_id=goods_main_id, style_id=payload.style_id
            ):
                raise InvalidGoodsReferenceError(
                    f"商品 {goods_main_id} 不包含款式 {payload.style_id}",
                    details={
                        "goods_main_id": str(goods_main_id),
                        "style_id": str(payload.style_id),
                    },
                )
        else:
            goods_main_id = await self._repo.resolve_owner_goods_id(payload.style_id)

        # 商品明细（传了才校验）：款式集合按上面定下的归属商品算；promotion.sku_id 取主款式那一行
        item_rows: list[tuple[UUID, UUID]] | None = None
        sku_id = payload.sku_id
        if payload.items is not None:
            item_rows = await self._validate_goods_items(
                goods_main_id=goods_main_id, style_id=payload.style_id, items=payload.items
            )
            main_sku_id = next(s for st, s in item_rows if st == payload.style_id)
            if sku_id is not None and sku_id != main_sku_id:
                raise InvalidSkuReferenceError(
                    f"SKU {sku_id} 与明细里主款式选的 SKU 不一致",
                    details={"sku_id": str(sku_id), "item_sku_id": str(main_sku_id)},
                )
            if sku is None:
                sku = await self._sku_repo.get_by_id(main_sku_id)
            sku_id = main_sku_id

        blogger = await self._blogger_repo.get_by_id(payload.blogger_id)
        if blogger is None:
            raise InvalidBloggerReferenceError(f"博主 {payload.blogger_id} 不存在或已删除")

        # 2. 字段写权限（quote_amount 可写）；收件三项过写权限并规范化电话
        await self._check_amount_write_permission(payload, user)
        payload = await self._normalize_receiver(payload, user)

        # 3. 取 tenant_code 用于 internal_code 前缀
        tenant_code = await self._get_tenant_code(user.tenant_id)

        # 合作日期 = 建单当天，服务端定（PRD 改动 5：自动生成、不可改）。
        # 人工录入时不该能挑日期 —— 日期往前挑会改掉 internal_code 的序列段，
        # 也会让这单落进已经对过账的区间。
        # Excel 导入走的是 importer 适配器，那条路径保留传入日期，否则导历史数据没法用。
        cooperation_date = get_today()

        # 4. 序列号原子获取（FB2）
        next_seq = await self._repo.next_internal_sequence(
            tenant_id=user.tenant_id,
            date_key=cooperation_date,
        )
        internal_code = format_internal_code(
            tenant_code=tenant_code,
            cooperation_date=cooperation_date,
            sequence=next_seq,
        )

        # 5. 快照字段计算
        quote_amount = (
            payload.quote_amount
            if payload.quote_amount is not None
            else (blogger.quote if blogger.quote is not None else None)
        )
        if quote_amount is None:
            # 业务规则 BR-U04-10：blogger.quote 为 NULL 时 PR 必须显式传值
            raise PublishUrlRequiredError(  # 复用 422 类异常通用消息
                "blogger.quote 为空时必须显式传 quote_amount",
                details={"field": "quote_amount"},
            )

        # 合作模式决定成本怎么取，后端强制覆盖前端传值（PRD 模块二硬约束）
        quote_amount, cost_snapshot = await self._resolve_mode_costs(
            mode=payload.cooperation_mode,
            quote_amount=quote_amount,
            goods_main_id=goods_main_id,
            sku=sku,
        )
        style_short_name = style.short_name or style.style_name

        # 6. 创建实体
        promotion = Promotion(
            style_id=payload.style_id,
            sku_id=sku_id,
            goods_main_id=goods_main_id,
            blogger_id=payload.blogger_id,
            pr_id=user.id,
            internal_code=internal_code,
            style_code_snapshot=style.style_code,
            style_short_name_snapshot=style_short_name,
            quote_amount=quote_amount,
            cost_snapshot=cost_snapshot,
            cooperation_mode=payload.cooperation_mode.value,
            return_shipping_fee=payload.return_shipping_fee,
            platform=payload.platform,
            cooperation_date=cooperation_date,
            scheduled_publish_date=payload.scheduled_publish_date,
            note_title=payload.note_title,
            remark=payload.remark,
            source_extra=dict(payload.source_extra or {}),
            receiver_name=payload.receiver_name,
            receiver_phone=payload.receiver_phone,
            receiver_address=payload.receiver_address,
            publish_status=PublishStatus.UNPUBLISHED.value,
            recall_status=RecallStatus.NOT_RECALLED.value,
            settlement_status=SettlementStatus.NOT_REVIEWED.value,
            is_active=True,
        )
        self._repo.add(promotion)
        await self._session.flush()
        if item_rows is not None:
            await self._items_repo.replace(
                tenant_id=user.tenant_id, promotion_id=promotion.id, rows=item_rows
            )

        # 7. 重复检测（EP05-S04 warning，非阻塞）
        duplicates = await self._repo.find_active_duplicate(
            style_id=payload.style_id,
            blogger_id=payload.blogger_id,
            exclude_id=promotion.id,
        )

        # 8. 审计：脱敏
        after_marker: dict[str, Any] = {
            "internal_code": internal_code,
            "publish_status": PublishStatus.UNPUBLISHED.value,
            "cooperation_mode": payload.cooperation_mode.value,
        }
        if quote_amount is not None:
            after_marker["quote_amount_changed"] = True
        if cost_snapshot is not None:
            after_marker["cost_snapshot_changed"] = True

        await self._audit.log(
            action="promotion.create",
            resource="promotion",
            resource_id=promotion.id,
            after=after_marker,
            user_id=user.id,
        )
        if autocommit:
            await self._session.commit()

        # 9. 返回（含重复警告）
        response = await self._to_response(promotion, user)
        if duplicates:
            response = response.model_copy(
                update={
                    "duplicate_warnings": [
                        PromotionDuplicateWarning(
                            promotion_id=d.id,
                            internal_code=d.internal_code,
                            publish_status=d.publish_status,
                            cooperation_date=d.cooperation_date,
                        )
                        for d in duplicates
                    ]
                }
            )
        return response

    # ============================================================
    # CRUD: update
    # ============================================================

    async def update_promotion(
        self,
        promotion_id: UUID,
        payload: PromotionUpdate,
        user: User,
    ) -> PromotionResponse:
        """部分更新（PATCH）。

        ``source_extra`` 按键合并（7a-5，``domain.merge_source_extra``）：补丁里值为 null
        或空白 = 删这个键，没出现的键不动。合并基于这次请求刚读出的行，所以「录入信息」
        弹窗开着期间仓库回填的发货单号不会被 PR 的旧快照冲掉；毫秒级的并发与其他字段
        一样不加锁。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        # 顺序（流程线 7.1）：404 → 退役键 422 → 字段写权限 → 其余校验
        self._reject_retired_source_extra_keys(payload)

        # 金额时间线的「更新前」快照必须在这里取 —— 下面补合作模式那一步就会改成本，
        # 等到算 changes 时拿到的已经是中间值了。
        amount_before = self._amount_snapshot(promotion)

        # 字段写权限；收件三项过写权限并规范化电话（之后的比对与落库都用规范化值）
        await self._check_amount_write_permission(payload, user)
        payload = await self._normalize_receiver(payload, user)

        # 合作模式：空值可以补一次（历史导入数据没有这个信息），有值就锁死。
        # PRD 的「生成后不可修改」靠这里拦，不靠前端禁用 —— 接口直接传值一样挡住。
        if "cooperation_mode" in payload.model_fields_set and payload.cooperation_mode is not None:
            if promotion.cooperation_mode is None:
                promotion.cooperation_mode = payload.cooperation_mode.value
                # 补上模式后成本口径才成立，按新模式初始化一次样品成本与服务费
                promotion.quote_amount, promotion.cost_snapshot = await self._resolve_mode_costs(
                    mode=payload.cooperation_mode,
                    quote_amount=promotion.quote_amount,
                    goods_main_id=promotion.goods_main_id,
                    sku=None,
                )
            elif promotion.cooperation_mode != payload.cooperation_mode.value:
                raise CooperationModeImmutableError(
                    "合作模式在单据生成后不可修改",
                    details={
                        "current": promotion.cooperation_mode,
                        "attempted": payload.cooperation_mode.value,
                    },
                )

        # SKU 改了重新校验
        if (
            "sku_id" in payload.model_fields_set
            and payload.sku_id is not None
            and payload.sku_id != promotion.sku_id
        ):
            sku = await self._sku_repo.get_by_id(payload.sku_id)
            if sku is None or sku.style_id != promotion.style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {payload.sku_id} 不存在或不属于款式 {promotion.style_id}"
                )

        # 商品归属改了同样要校验包含关系（款式不可改，所以只校验新商品含旧款式）
        if (
            "goods_main_id" in payload.model_fields_set
            and payload.goods_main_id is not None
            and payload.goods_main_id != promotion.goods_main_id
            and not await self._repo.goods_contains_style(
                goods_main_id=payload.goods_main_id, style_id=promotion.style_id
            )
        ):
            raise InvalidGoodsReferenceError(
                f"商品 {payload.goods_main_id} 不包含款式 {promotion.style_id}",
                details={
                    "goods_main_id": str(payload.goods_main_id),
                    "style_id": str(promotion.style_id),
                },
            )

        changes = compute_promotion_changes(promotion, payload)
        if not changes:
            return await self._to_response(promotion, user)

        # 应用变更。cooperation_mode 上面已经按「空值补一次、有值锁死」处理过，
        # 这里不能再 setattr —— 否则会把枚举对象写回去，也会绕过那段拦截。
        for field in changes:
            if field == "cooperation_mode":
                continue
            if field == "source_extra":
                # 按键合并，不整包 setattr —— 整包会删掉表单上没有的键（7a-5）
                promotion.source_extra = merge_source_extra(
                    promotion.source_extra, payload.source_extra or {}
                )
                continue
            new_value = getattr(payload, field)
            setattr(promotion, field, new_value)

        # 成本的两条硬规则在变更应用之后强制一次：PATCH 可以直接传 quote_amount /
        # cost_snapshot，而 PRD 要求「寄拍样品成本恒 0、置换服务费恒 0」不管谁传什么。
        # 注意只强制这两条 —— 送拍/置换的样品成本 PRD 允许 PR 手动微调，不能重算覆盖。
        self._enforce_mode_costs(promotion)

        await self._session.flush()

        # 金额时间线：只记净变更（PRD 第 10 节第 14 条）。
        # audit 那边继续只记 *_changed 标记 —— 理由见 domain.PROMOTION_SENSITIVE_VALUE_FIELDS。
        self._log_amount_changes(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            user_id=user.id,
            before=amount_before,
            after=self._amount_snapshot(promotion),
            payload=payload,
        )

        # 审计：仅敏感字段 + 敏感值脱敏
        audit_changes = build_promotion_audit_changes(changes)
        if audit_changes:
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for k, v in audit_changes.items():
                if isinstance(v, dict):
                    before[k] = v["before"]
                    after[k] = v["after"]
                else:
                    after[k] = v
            await self._audit.log(
                action="promotion.update",
                resource="promotion",
                resource_id=promotion.id,
                before=before or None,
                after=after,
                user_id=user.id,
            )
        await self._session.commit()
        return await self._to_response(promotion, user)

    async def update_warehouse_waybill(
        self, promotion_id: UUID, payload: PromotionWarehouseWaybillRequest, user: User
    ) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        source_extra = dict(promotion.source_extra or {})
        source_extra["发货单号"] = payload.waybill.strip()
        promotion.source_extra = source_extra
        await self._audit.log(
            action="promotion.warehouse_waybill.update",
            resource="promotion",
            resource_id=promotion.id,
            after={"waybill_changed": True},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(promotion, user)

    # ============================================================
    # Read
    # ============================================================

    async def get_promotion(self, promotion_id: UUID, user: User) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        return await self._to_response(promotion, user)

    async def list_promotions(
        self,
        *,
        filters: ApiPromotionListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> PromotionPage:
        """列表 + CTE（FB8 + Pattern P-U04-04）.

        match 降级：业务未匹配返回空数组；系统失败让异常自然冒泡（不 try/except）。
        """
        today = get_today()

        repo_filters = RepoPromotionListFilters(
            keyword=filters.keyword,
            publish_status=(filters.publish_status.value if filters.publish_status else None),
            recall_status=(filters.recall_status.value if filters.recall_status else None),
            settlement_status=(
                filters.settlement_status.value if filters.settlement_status else None
            ),
            platform=filters.platform,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            pr_id=filters.pr_id,
            cooperation_date_from=filters.cooperation_date_from,
            cooperation_date_to=filters.cooperation_date_to,
            scheduled_publish_date_from=filters.scheduled_publish_date_from,
            scheduled_publish_date_to=filters.scheduled_publish_date_to,
            is_active=filters.is_active,
            only_dual_platform=filters.only_dual_platform,
            is_hit=filters.is_hit,
            hit_threshold=HIT_THRESHOLD_LIKE_COUNT,
            has_print_address=filters.has_print_address,
            has_waybill=filters.has_waybill,
        )

        # 阈值读租户配置（后台可改），整页共用一次查询
        thresholds = await UrgeService(self._session).get_urge_thresholds(user.tenant_id)
        rows, total = await self._repo.list_with_cte(
            tenant_id=user.tenant_id,
            filters=repo_filters,
            page=page,
            page_size=page_size,
            today=today,
            urge_threshold_days=thresholds.urge_days,
            important_threshold_days=thresholds.important_days,
        )

        promotion_search_results_count.observe(total)

        attachment_refs = await self._repo.get_payment_attachment_refs(
            tenant_id=user.tenant_id,
            promotion_ids=[row.promotion.id for row in rows],
        )
        # 商品明细整页一次查（流程线 7.3）
        page_items = await self._items_repo.list_by_promotions([row.promotion.id for row in rows])

        # 用 CTE 计算结果填充响应（避免重复计算 urge_status / dual_platform）
        items = [
            await self._to_response(
                row.promotion,
                user,
                today=today,
                urge_status_override=row.urge_status,
                dual_platform_override=row.dual_platform,
                attachment_refs=attachment_refs.get(row.promotion.id),
                style_main_image_key=row.style_main_image_key,
                style_main_image_preloaded=True,
                goods_code=row.goods_code,
                goods_is_suit=row.goods_is_suit,
                display_short_name=row.display_short_name,
                goods_title=row.goods_title,
                goods_short_name=row.goods_short_name,
                goods_preloaded=True,
                items=page_items.get(row.promotion.id, []),
            )
            for row in rows
        ]
        return PromotionPage(items=items, total=total, page=page, page_size=page_size)

    # ============================================================
    # 软停用 / 内部 API
    # ============================================================

    async def soft_delete_promotion(self, promotion_id: UUID, user: User) -> None:
        """通用软停用：is_active=false（与状态机正交）."""
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        deactivated = await self._repo.soft_deactivate(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
        )
        if deactivated is None:
            raise StateTransitionConflictError(
                "推广已被停用或软删，请刷新后重试",
            )

        await self._audit.log(
            action="promotion.delete",
            resource="promotion",
            resource_id=promotion_id,
            user_id=user.id,
        )
        await self._session.commit()


__all__ = ["PromotionService"]
