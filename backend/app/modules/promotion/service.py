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

按职责拆成 mixin（``service_parts/``）：本文件留 CRUD；共用依赖与 helper 在 ``base.py``，
收款码 / 发布与取消 / 召回 / 审核与重提 / 数据与复盘 / 发货与仓库各一个文件。``PromotionService`` 的名字与路径不变。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.metrics import (
    promotion_search_results_count,
)
from app.modules.auth.models import User
from app.modules.flow.line_events import (
    PROMOTION_SOURCE_DIRECT,
    PROMOTION_SOURCE_NEGOTIATION,
    LineEventRepository,
    LineEventType,
)
from app.modules.flow.matrix import Level, cell_level, ensure_patch_allowed, require
from app.modules.importer.access import SCOPE_PROMOTION_CREATE
from app.modules.importer.access import can_write as can_import_write
from app.modules.metric_config.service import PlatformMetricSettingService
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
    ShipStatus,
)
from app.modules.promotion.exceptions import (
    CooperationModeImmutableError,
    FieldPermissionDenied,
    InvalidBloggerReferenceError,
    InvalidGoodsReferenceError,
    InvalidSkuReferenceError,
    InvalidStyleReferenceError,
    PromotionNotFoundError,
    PromotionSettlementInProgressError,
    PublishUrlRequiredError,
    StateTransitionConflictError,
)
from app.modules.promotion.flow_matrix import PROMOTION_MATRIX
from app.modules.promotion.legacy_settings import HIT_THRESHOLD_LIKE_COUNT
from app.modules.promotion.models import (
    Promotion,
)
from app.modules.promotion.repository import (
    PromotionListFilters as RepoPromotionListFilters,
)
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionCreate,
    PromotionDuplicateWarning,
    PromotionPage,
    PromotionResponse,
    PromotionUpdate,
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
from app.modules.promotion.service_parts.settlement_link import PromotionSettlementLinkMixin
from app.modules.promotion.service_parts.shipping import PromotionShippingMixin
from app.modules.promotion.urge_calculator import (
    get_today,
)
from app.modules.urge.service import UrgeService

# 进行中的结款单状态（收尾 L3 停用门）；推广模块不 import finance，同 payment_qr 的写法
_SETTLEMENT_IN_PROGRESS = frozenset({"待付款", "待财务付款"})


class PromotionService(
    PromotionPaymentQrMixin,
    PromotionPublishMixin,
    PromotionRecallMixin,
    PromotionReviewMixin,
    PromotionRetroMixin,
    PromotionSettlementLinkMixin,
    PromotionShippingMixin,
):
    """推广合作业务服务。"""

    # ============================================================
    # CRUD: create
    # ============================================================

    async def create_promotion(
        self,
        payload: PromotionCreate,
        user: User,
        *,
        autocommit: bool = True,
        ship_status: ShipStatus | None = None,
        line_id: UUID | None = None,
        negotiation_id: UUID | None = None,
        actor_id: UUID | None = None,
        reviewed_negotiation_copy: bool = False,
    ) -> PromotionResponse:
        """EP05-S02 创建推广 + 自动 internal_code + 重复检测.

        Args:
            autocommit: 默认 True，HTTP 路径下自己提交。谈款审核通过时要在同一个事务里
                「改谈款状态 + 建推广单」，那边传 False 由调用方统一提交 ——
                否则中间失败会留下「审核通过但没有推广单」的单据。
            ship_status: 发货状态初值（流程线 S1）。HTTP 新建按 ``need_shipping`` 传「待发货」或 None，
                谈款审核通过传「待发货」；None = 历史单口径，不进待推送队列（11-58）。
            line_id: 主线号（流程线 6.2）。谈款定稿传谈款的号；None = 库默认值发号（直接新建）。
                ``uq_promotion_line`` 保证一条线只有一张推广单，重复会在 flush 时 IntegrityError。
            negotiation_id: 来自哪张谈款（只进 ``promotion.created`` 的 payload，事件只挂推广单）。
            actor_id: 事件的动作人；None = ``user``。谈款路径 ``user`` 是对接 PR（建单人），
                动作人是确认的那个人。
            reviewed_negotiation_copy: 只有谈款定稿传 True：服务费、收件三项是从两级审过的谈款复制的，
                不算 ``user``（谈款 PR）的新写入，跳过按 ``user`` 的字段写权限（评审 L2、11-57 ①：
                PR 被收角色后主管仍能代确认）；确认人的写权限由谈款那边先查过。电话规范化照做。
                直接新建（``POST /``）传 False，检查不变。
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
        #    谈款定稿复制的是审过的条款，不按谈款 PR 查写权限（评审 L2）
        if not reviewed_negotiation_copy:
            await self._check_amount_write_permission(payload, user)
        payload = await self._normalize_receiver(
            payload, user, check_write=not reviewed_negotiation_copy
        )

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
            ship_status=ship_status.value if ship_status is not None else None,
            publish_status=PublishStatus.UNPUBLISHED.value,
            recall_status=RecallStatus.NOT_RECALLED.value,
            settlement_status=SettlementStatus.NOT_REVIEWED.value,
            is_active=True,
        )
        if line_id is not None:
            # 不传就别赋 None：显式 None 会盖掉库默认值 gen_random_uuid()
            promotion.line_id = line_id
        self._repo.add(promotion)
        await self._session.flush()
        if item_rows is not None:
            await self._items_repo.replace(
                tenant_id=user.tenant_id, promotion_id=promotion.id, rows=item_rows
            )

        # 时间线（流程线 6.2、6.3）：同事务写 promotion.created，只挂推广单；line_id 用 flush 回读的值
        created_payload: dict[str, Any] = {
            "source": (
                PROMOTION_SOURCE_NEGOTIATION
                if negotiation_id is not None
                else PROMOTION_SOURCE_DIRECT
            )
        }
        if negotiation_id is not None:
            created_payload["negotiation_id"] = str(negotiation_id)
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.PROMOTION_CREATED,
            actor_id=actor_id if actor_id is not None else user.id,
            promotion_id=promotion.id,
            payload=created_payload,
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
        # 停用与审核建结款单互斥（评审 M1）：锁前不判矩阵、不查结款，锁后用当前快照。
        promotion = (
            await self._repo.get_for_deactivation(promotion_id, tenant_id=user.tenant_id)
            if "is_active" in payload.model_fields_set and payload.is_active is False
            else await self._repo.get_by_id(promotion_id)
        )
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        # 顺序（流程线 7.1）：404 → 退役键 422 → 合作模式改成另一个值 409 → 已入矩阵的字段（越权全列 403）
        # → 字段写权限 → 其余校验
        self._reject_retired_source_extra_keys(payload)
        managed = PROMOTION_MATRIX.patch_groups.keys() & payload.model_fields_set
        if "cooperation_mode" in managed:
            attempted = payload.cooperation_mode.value if payload.cooperation_mode else None
            if attempted is not None and promotion.cooperation_mode not in (None, attempted):
                # 有值之后任何人都不能改成另一个值（²³），先于矩阵：主管、管理员也是 409 而不是 403
                raise CooperationModeImmutableError(
                    "合作模式在单据生成后不可修改",
                    details={"current": promotion.cooperation_mode, "attempted": attempted},
                )
            if attempted is None or attempted == promotion.cooperation_mode:
                # 传 null 或同值是空操作（下面也不落库），不按「改合作模式」判权
                managed = managed - {"cooperation_mode"}
        if managed:
            ensure_patch_allowed(
                await self._flow_actor(user), await self._promotion_doc(promotion), managed
            )
        # 停用门（收尾 L3）：与 DELETE 同一道，矩阵之后判
        if "is_active" in managed and payload.is_active is False and promotion.is_active:
            await self._ensure_no_settlement_in_progress(promotion)

        # 金额时间线的「更新前」快照必须在这里取 —— 下面补合作模式那一步就会改成本，
        # 等到算 changes 时拿到的已经是中间值了。
        amount_before = self._amount_snapshot(promotion)
        mode_before = promotion.cooperation_mode

        # 字段写权限；收件三项过写权限并规范化电话（之后的比对与落库都用规范化值）
        await self._check_amount_write_permission(payload, user)
        payload = await self._normalize_receiver(payload, user, check_write=True)

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
        # 合作模式在上面已经补到实例上，compute 拿补后的值比，看不出变化；按前值补进 changes，
        # 否则只补合作模式的 PATCH 会在下面提前返回、不提交，也不写时间线和审计（C1）
        if mode_before != promotion.cooperation_mode:
            changes["cooperation_mode"] = {
                "before": mode_before,
                "after": promotion.cooperation_mode,
            }
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

        # 改归属商品（11-53）：推送前（待发货 / 历史单）换了商品，旧明细按旧成员选的，
        # 同事务删掉、推送时按新商品补选；推送后明细是实际发出的货，只改归属不动明细。
        # 先删再同步 sku_id：同一请求带了 sku_id 时主款式那一行按新值写回。
        clear_items = "goods_main_id" in changes and promotion.ship_status in (
            None,
            ShipStatus.PENDING.value,
        )
        # 时间线 ship.items_changed 的前值：要在改明细之前取（流程线 6.3、D13）
        items_before = (
            await self._items_display(promotion.id) if clear_items or "sku_id" in changes else None
        )
        if clear_items:
            await self._items_repo.replace(
                tenant_id=promotion.tenant_id, promotion_id=promotion.id, rows=[]
            )

        # sku_id 是主款式那一行的颜色尺码（5.4）：同步明细，有就改、没有就插
        if "sku_id" in changes:
            await self._items_repo.set_style_sku(
                tenant_id=promotion.tenant_id,
                promotion_id=promotion.id,
                style_id=promotion.style_id,
                sku_id=promotion.sku_id,
            )

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

        # 时间线（流程线 6.3）：promotion.updated / ship.receiver_changed / ship.items_changed，同事务
        await self._append_patch_events(
            promotion,
            user,
            changes=changes,
            mode_before=mode_before,
            amount_before=amount_before,
        )
        if items_before is not None:
            await self._append_items_changed(
                promotion,
                user,
                before=items_before,
                reason="换归属商品，推送前的明细已清空" if clear_items else "改主款颜色尺码",
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

    async def replace_items(
        self, promotion_id: UUID, items: list[GoodsItemIn], user: User
    ) -> PromotionResponse:
        """``PUT /{id}/items``：整组替换颜色尺码明细，不换商品（流程线 7.3）。

        顺序：404 → 矩阵 ``goods_items`` 不是「改」→ 403 ``FIELD_PERMISSION_DENIED``（fields=["goods_items"]）
        → 明细校验（款式集合 = 归属商品的成员、每行有 sku 且属于该款）422。同步 ``promotion.sku_id`` = 主款式那行。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        actor = await self._flow_actor(user)
        doc = await self._promotion_doc(promotion)
        if cell_level(actor, doc, "field", "goods_items") is not Level.EDIT:
            raise FieldPermissionDenied(fields=["goods_items"], entity="promotion")

        rows = await self._validate_goods_items(
            goods_main_id=promotion.goods_main_id, style_id=promotion.style_id, items=items
        )
        before = [
            [str(v.style_id), str(v.sku_id)]
            for v in (await self._items_repo.list_by_promotions([promotion.id])).get(
                promotion.id, []
            )
        ]
        items_before = await self._items_display(promotion.id)
        await self._items_repo.replace(
            tenant_id=promotion.tenant_id, promotion_id=promotion.id, rows=rows
        )
        main_sku_id = next((sku for style, sku in rows if style == promotion.style_id), None)
        if main_sku_id is not None:
            promotion.sku_id = main_sku_id
        await self._session.flush()
        # 时间线（流程线 6.3）：整组替换写 ship.items_changed，前后一样不写
        await self._append_items_changed(
            promotion, user, before=items_before, reason="改颜色尺码明细"
        )
        await self._audit.log(
            action="promotion.items.replace",
            resource="promotion",
            resource_id=promotion.id,
            before={"items": before},
            after={"items": [[str(style), str(sku)] for style, sku in rows]},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(promotion, user, actor=actor)

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
            ship_status=filters.ship_status,
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
        # 平台指标（折算系数）整页读一次（7e R7）
        metric_settings = await PlatformMetricSettingService(self._session).get_settings(
            user.tenant_id
        )

        attachment_refs = await self._repo.get_payment_attachment_refs(
            tenant_id=user.tenant_id,
            promotion_ids=[row.promotion.id for row in rows],
        )
        # 商品明细整页一次查（流程线 7.3）
        page_ids = [row.promotion.id for row in rows]
        page_items = await self._items_repo.list_by_promotions(page_ids)
        # 矩阵 ui 只用本页数据算（7.1）：当前用户、谈款人、套装成员各一次
        actor = await self._flow_actor(user)
        negotiators = await self._repo.negotiator_ids(page_ids)
        members_by_goods = await self._items_repo.members_by_goods(
            list({r.promotion.goods_main_id for r in rows if r.promotion.goods_main_id is not None})
        )
        # goods_members 的款式名：本页全部成员款式一次查
        member_style_ids: set[UUID] = set()
        for r in rows:
            member_style_ids.update(await self._members_of(r.promotion, members_by_goods))
        style_names = await self._items_repo.style_names(list(member_style_ids))
        user_names = await self._repo.user_names(
            [r.promotion.ship_pushed_by for r in rows if r.promotion.ship_pushed_by is not None]
        )

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
                actor=actor,
                view="list",
                stage=row.stage,
                negotiators=negotiators,
                members_by_goods=members_by_goods,
                user_names=user_names,
                style_names=style_names,
                metric_settings=metric_settings,
            )
            for row in rows
        ]
        # 页级标记（收尾 L3）：「金额改动」页签与菜单只看它，与行上的报价值无关
        page_actions: dict[str, Any] = {}
        if await self.amount_log_readable(user):
            page_actions["amount_log"] = {"state": "enabled"}
        # 「新建推广」「导入站外推广」（流程线 PR-5，8.4）：与建单接口、manual_promotion 上传同一判定
        if actor.perms.has(SCOPE_PROMOTION_CREATE, "write"):
            page_actions["create"] = {"state": "enabled"}
        if can_import_write(actor.perms, "manual_promotion"):
            page_actions["import"] = {"state": "enabled"}
        return PromotionPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            ui={"actions": page_actions},
        )

    # ============================================================
    # 软停用 / 内部 API
    # ============================================================

    async def soft_delete_promotion(self, promotion_id: UUID, user: User) -> None:
        """通用软停用：is_active=false（与状态机正交）.

        顺序：404 → 矩阵 ``deactivate``（已停用 / 已删除 422；A ~ D、G 列主管能停，结款单生成后只有
        管理员，11-15；不够 403）→ 停用门（结款单进行中 409，收尾 L3）→ 条件 UPDATE（并发下 0 行 409）
        → 时间线 ``promotion.updated``（diff ``is_active``，同 PATCH 的写法）→ 审计 → 提交。
        """
        promotion = await self._repo.get_for_deactivation(promotion_id, tenant_id=user.tenant_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        require(await self._flow_actor(user), await self._promotion_doc(promotion), "deactivate")
        await self._ensure_no_settlement_in_progress(promotion)
        tenant_id, line_id = promotion.tenant_id, promotion.line_id

        deactivated = await self._repo.soft_deactivate(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
        )
        if deactivated is None:
            raise StateTransitionConflictError(
                "推广已被停用或软删，请刷新后重试",
            )

        await LineEventRepository(self._session).append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=LineEventType.PROMOTION_UPDATED,
            actor_id=user.id,
            promotion_id=promotion_id,
            payload={"diff": {"is_active": {"before": True, "after": False}}},
        )

        await self._audit.log(
            action="promotion.delete",
            resource="promotion",
            resource_id=promotion_id,
            user_id=user.id,
        )
        await self._session.commit()

    async def _ensure_no_settlement_in_progress(self, promotion: Promotion) -> None:
        """停用门（收尾 L3）：结款单待付款 / 待财务付款时不能停用，管理员也一样 → 409。

        停用后推广单的状态 UPDATE 都带 ``is_active = true``，结款单就驳不掉了。用户停用的两条路
        （``DELETE /{id}``、``PATCH`` 带 ``is_active = false``）都过这里；导入、迁移这类系统路径不经过。
        """
        settlement = await self._repo.settlement_brief(
            tenant_id=promotion.tenant_id, promotion_id=promotion.id
        )
        if settlement is not None and settlement.settlement_status in _SETTLEMENT_IN_PROGRESS:
            raise PromotionSettlementInProgressError(
                details={
                    "settlement_no": settlement.settlement_no,
                    "settlement_status": settlement.settlement_status,
                }
            )


__all__ = ["PromotionService"]
