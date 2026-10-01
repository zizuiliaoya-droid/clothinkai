"""列表接口的字段投影完整性。

``PromotionRepository.list_with_cte`` 把 raw row 重组成 ORM 实例时用的是一份**手写的
列白名单**。白名单漏了某一列，那一列在列表接口里就恒为 null —— 详情接口正常，列表
接口静默丢字段，前端表格显示一片「—」，不报错、不告警。

这类 bug 已经真实发生过：批次 2a/2b 加的 ``cooperation_mode`` / ``return_waybill`` /
``total_promo_cost`` / ``review_reason_category`` 都没进白名单，上线后推广列表那几列
一直是空的。所以这里不只测具体字段，而是拿 ORM 的列定义去比对 —— 以后谁加列漏了
白名单，这条就会红。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import CooperationMode, PublishStatus, SettlementStatus
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import PromotionListFilters as RepoFilters
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 这些列不参与列表重组是合理的，逐个说明原因，免得下次有人盲目加进去。
_INTENTIONALLY_ABSENT: set[str] = {
    # 生成列。SQLAlchemy 不允许给 Computed 列赋值，重组时必须跳过；
    # 响应里的 total_promo_cost 由 service 从 quote/cost/运费 现算或走详情路径。
    "total_promo_cost",
}


class TestListProjection:
    async def test_every_orm_column_is_carried_to_list_rows(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """列表重组出的 ORM 实例，每个列属性都要和 DB 里的值一致。

        做法是先用工厂建单、再走列表查、然后逐列比对 ORM 实例与直接 get 的实例。
        漏掉的列在列表实例上是 None，在 get 出来的实例上有值，差异一目了然。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="LP_PROJ")
            blogger = await blogger_factory.blogger()
            promo = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                quote_amount=Decimal("800.00"),
                cost_snapshot=Decimal("0"),
                publish_status=PublishStatus.PUBLISHED.value,
                settlement_status=SettlementStatus.PAID.value,
            )
            # 工厂的字段清单是手写的，不认识的 kwarg 会被静默忽略 —— 所以这些列必须
            # 直接 UPDATE 落值。都给非默认值：只有这样「漏了白名单」才表现为
            # listed=None vs expected=有值；留在 NULL 的话两边都是 None，测不出来。
            await session.execute(
                sa_text(
                    """
                    UPDATE promotion SET
                      cooperation_mode = :mode,
                      return_shipping_fee = 12.50,
                      return_waybill = 'SF123456',
                      review_reason_category = '延迟发文',
                      like_count = 999,
                      collect_count = 88,
                      comment_count = 7,
                      metrics_recorded_at = NOW(),
                      retro_status = '待复盘',
                      retro_confirmed_at = NOW(),
                      retro_confirmed_by = :uid,
                      in_store_order = true,
                      note_title = '测试笔记',
                      remark = '备注',
                      cancel_reason = '取消原因',
                      recall_reason = '召回原因',
                      publish_url = 'https://example.com/n/1',
                      actual_publish_date = CURRENT_DATE,
                      scheduled_publish_date = CURRENT_DATE,
                      reviewed_by = :uid,
                      reviewed_at = NOW(),
                      review_action = 'approve',
                      review_reason = '审核说明',
                      source_extra = '{"打单地址": "xx"}'::jsonb
                    WHERE id = :pid
                    """
                ),
                {"mode": CooperationMode.CONSIGNMENT.value, "uid": pr.id, "pid": promo.id},
            )
            pid = promo.id
            await session.commit()

            repo = PromotionRepository(session)
            rows, total = await repo.list_with_cte(
                tenant_id=tenant_a.id,
                filters=RepoFilters(),
                page=1,
                page_size=20,
                today=get_today(),
                urge_threshold_days=10,
                important_threshold_days=3,
            )
            assert total >= 1
            listed = next(r.promotion for r in rows if r.promotion.id == pid)

            # 拿数据库原始行做基准，不用 ORM 实例 —— 重组出来的实例是 transient，
            # 和 session 里的持久实例混着比会撞上懒加载（MissingGreenlet）。
            expected = (
                (
                    await session.execute(
                        sa_text("SELECT * FROM promotion WHERE id = :pid"), {"pid": pid}
                    )
                )
                .mappings()
                .one()
            )

            missing: list[str] = []
            for col in Promotion.__table__.columns:
                name = col.name
                if name in _INTENTIONALLY_ABSENT:
                    continue
                if getattr(listed, name, None) != expected[name]:
                    missing.append(name)
            assert missing == [], f"列表重组漏了这些列（恒为 null）：{missing}"
        finally:
            tenant_id_ctx.reset(token)
