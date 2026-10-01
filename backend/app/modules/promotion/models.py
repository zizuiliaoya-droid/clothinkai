"""U04 ORM 模型：Promotion + PromotionSequence。

按 functional-design/domain-entities.md §3-§5 定义。

继承 ``TenantScopedModel``：
- 自动 ``id`` (UUID PK) + ``tenant_id`` (UUID FK + ORM 钩子)
- 自动 ``created_at`` / ``updated_at``
- 启用 RLS（migration 通过 ``rls.enable_rls_sql`` 配置）

业务键唯一约束：
- ``promotion``：``(tenant_id, internal_code) WHERE is_active=true``
- ``promotion_sequence``：``(tenant_id, date_key)``（无 is_active；表本身不软删）

GIN trgm 索引（U04 强制建，对应 NFR §5 模糊搜索路径）：
- ``idx_promotion_internal_code_trgm``
- ``idx_promotion_style_code_snapshot_trgm``
- ``idx_promotion_short_name_trgm``

注：U04 不设 ``is_deleted`` 字段；删除走 ``publish_status="已删除"`` 状态机路径。
``is_active`` 用于软停用（与状态机正交）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel

# ---------------------------------------------------------------------------
# Promotion（推广合作）
# ---------------------------------------------------------------------------


class Promotion(TenantScopedModel):
    """推广合作（业务核心表）。

    28 业务字段（不含继承的 id / tenant_id / created_at / updated_at）。
    """

    __tablename__ = "promotion"

    # total_promo_cost 是数据库生成列。SQLAlchemy 默认把这类列当「稍后再取」，
    # 首次访问时补发一条 SELECT —— 在 async session 里那条隐式 IO 会抛 MissingGreenlet。
    # eager_defaults 让 INSERT/UPDATE 直接带 RETURNING 把值取回来，避免延迟加载。
    # RUF012 建议标 ClassVar，但 DeclarativeBase 把 __mapper_args__ 声明成实例变量，
    # 加了 ClassVar mypy 会报 override 冲突。SQLAlchemy 的约定写法就是不带注解。
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    # --- 关联字段 ---
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sku.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # 这次推广是为哪个商品做的。款式既单卖又进套装时，只有 PR 知道推的是哪个，
    # 所以落库而不是在报表里按货号字典序猜（那样新建商品会让历史归属突然跳走）。
    # 可空：历史数据与「款式还没归到商品」的异常情况下，报表回落到兜底规则。
    goods_main_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="RESTRICT"),
        nullable=True,
    )
    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_qr_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # --- 业务键 ---
    internal_code: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- 快照字段（创建时一次性写入，不再重算）---
    style_code_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    style_short_name_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    quote_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # --- 合作模式与成本（PRD V1.4 模块二）---
    cooperation_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    """寄拍 / 送拍 / 置换。决定样品成本与博主服务费怎么取、审核通过后走哪个出口。

    可空仅为了容纳历史导入数据（5154 条「未发布」的老记录没有这个信息）。
    新建必填；一旦有值就锁死，service 层拦截修改 —— 单据定稿后改模式等于改了成本口径。
    """

    return_shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    """寄回运费。召回流程里由 PR 录入；寄拍模式下它是唯一计入成本的那一项。"""

    return_waybill: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """博主寄回衣服单号。

    寄拍模式的硬门槛：审核通过后没有这个单号就不允许流转到待财务付款
    （PRD 模块二「不上传单号财务看不到单据，禁止结款」）。送拍与置换不校验。
    """

    total_promo_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        Computed(
            "quote_amount + COALESCE(cost_snapshot, 0) + COALESCE(return_shipping_fee, 0)",
            persisted=True,
        ),
        nullable=False,
    )
    """站外推广成本 = 博主服务费 + 样品成本 + 寄回运费。

    数据库生成列，不由应用层赋值。PRD 要求「任意成本字段变更实时重算」，而写入路径有
    HTTP、Excel 导入、迁移三条，手动重算迟早漏一条，交给数据库才漏不掉。
    """

    # --- 业务字段 ---
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    cooperation_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    publish_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recall_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- 发布满 7 天的数据（PRD V1.4 改动 4：PR 录入点赞/收藏/评论 + 截图）---
    collect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """收藏数 / 评论数。

    转成 typed 列而不是继续塞 ``source_extra``：这三个指标是复盘的依据，要给人看、
    要进校验，JSONB 里存什么类型都行挡不住脏数据。``like_count`` 本来就是 typed，
    三个放一起才一致。

    注意**没有动** ``source_extra['点赞数']`` —— typed ``like_count`` 与它并存不同步
    是一个独立的待确认项（哪个为准还没定），这里不顺手改掉。
    """

    metrics_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """7 天数据截图。PRD 原文「发布满 7 天，PR 录入点赞/收藏/评论 + 截图」。

    必传，但不在 DB 层约束 —— 有大量历史单据永远走不到这一步，加 CHECK 会把它们
    一起卡住。门槛放在 ``record_metrics`` 里。
    """

    metrics_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """指标录入时间。之前系统靠 ``like_count IS NOT NULL`` 当「信息完整」的替代判断，
    那个在 like_count 合法为 0 时会误判；有了这个字段才说得清「录过没有」。"""

    # --- 复盘（PRD V1.4 改动 4）---
    retro_status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'未开始'")
    )
    """未开始 / 待复盘 / 待确认 / 已完成。第 4 个并行状态机。

    不复用 ``settlement_status``：那边 ``已付款`` 是终态，而且全系统有一批查询按
    ``settlement_status = '已付款'`` 过滤，塞进去会让它们漏掉进入复盘的单子。
    """

    retro_confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    retro_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    note_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 人工源列扩展（对齐 final.xlsx：颜色及规格/打单地址/发货单号/订单号/寄回单号/合作方式/合作形式/收藏数/评论数/博主风格 等）
    source_extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # --- U16 拍单 ---
    in_store_order: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # --- 三个状态字段 ---
    publish_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未发布'")
    )
    recall_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未召回'")
    )
    settlement_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未核查'")
    )

    # --- 审核相关 ---
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_reason_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """驳回原因分类：延迟发文 / 流量差补发 / 衣服未寄回（PRD 改动 5，驳回时必填）。"""

    # --- 通用 ---
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        # 业务键唯一（部分索引：仅 active 行参与唯一性）
        Index(
            "uq_promotion_internal_code",
            "tenant_id",
            "internal_code",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        # 列表过滤
        Index(
            "idx_promotion_tenant_active",
            "tenant_id",
            "is_active",
            "publish_status",
        ),
        Index("idx_promotion_pr", "tenant_id", "pr_id"),
        Index(
            "idx_promotion_payment_qr_attachment_id",
            "payment_qr_attachment_id",
            postgresql_where=text("payment_qr_attachment_id IS NOT NULL"),
        ),
        # 重复检测 + 按博主 / 款式查
        Index(
            "idx_promotion_blogger",
            "tenant_id",
            "blogger_id",
            "publish_status",
        ),
        Index(
            "idx_promotion_style",
            "tenant_id",
            "style_id",
            "publish_status",
        ),
        # 投产报表按商品聚合推广费
        Index(
            "idx_promotion_goods",
            "tenant_id",
            "goods_main_id",
            "publish_status",
        ),
        # 排序 + urge 计算
        Index(
            "idx_promotion_cooperation_date",
            "tenant_id",
            "cooperation_date",
        ),
        Index(
            "idx_promotion_scheduled_date",
            "tenant_id",
            "scheduled_publish_date",
        ),
        Index(
            "idx_promotion_publish_dates",
            "tenant_id",
            "publish_status",
            "scheduled_publish_date",
        ),
        # 财务 / 召回查询
        Index(
            "idx_promotion_settlement_status",
            "tenant_id",
            "settlement_status",
        ),
        Index(
            "idx_promotion_recall_status",
            "tenant_id",
            "recall_status",
        ),
        # CHECK 约束
        CheckConstraint(
            "like_count IS NULL OR like_count >= 0",
            name="ck_promotion_like_count_nonneg",
        ),
        CheckConstraint(
            "collect_count IS NULL OR collect_count >= 0",
            name="ck_promotion_collect_count_nonneg",
        ),
        CheckConstraint(
            "comment_count IS NULL OR comment_count >= 0",
            name="ck_promotion_comment_count_nonneg",
        ),
        CheckConstraint(
            "retro_status IN ('未开始', '待复盘', '待确认', '已完成')",
            name="ck_promotion_retro_status",
        ),
        Index("idx_promotion_retro_status", "tenant_id", "retro_status"),
        CheckConstraint(
            "quote_amount >= 0",
            name="ck_promotion_quote_amount_nonneg",
        ),
        CheckConstraint(
            "cost_snapshot IS NULL OR cost_snapshot >= 0",
            name="ck_promotion_cost_snapshot_nonneg",
        ),
        CheckConstraint(
            "cooperation_mode IS NULL OR cooperation_mode IN ('寄拍', '送拍', '置换')",
            name="ck_promotion_cooperation_mode",
        ),
        CheckConstraint(
            "return_shipping_fee IS NULL OR return_shipping_fee >= 0",
            name="ck_promotion_return_shipping_fee_nonneg",
        ),
        CheckConstraint(
            "review_reason_category IS NULL OR review_reason_category IN "
            "('延迟发文', '流量差补发', '衣服未寄回')",
            name="ck_promotion_review_reason_category",
        ),
        Index(
            "idx_promotion_return_waybill",
            "tenant_id",
            "return_waybill",
            postgresql_where=text("return_waybill IS NOT NULL"),
        ),
        Index(
            "idx_promotion_cooperation_mode",
            "tenant_id",
            "cooperation_mode",
            postgresql_where=text("cooperation_mode IS NOT NULL"),
        ),
        # GIN trgm 索引在 alembic migration 中通过 op.execute 创建：
        # idx_promotion_internal_code_trgm
        # idx_promotion_style_code_snapshot_trgm
        # idx_promotion_short_name_trgm
    )


# ---------------------------------------------------------------------------
# PromotionSequence（internal_code 序列号表）
# ---------------------------------------------------------------------------


class PromotionSequence(TenantScopedModel):
    """internal_code 序列号表（按 (tenant_id, date_key) 累计）。

    生成策略（FB2 修正）：
        INSERT ... ON CONFLICT (tenant_id, date_key) DO UPDATE
        SET last_seq = last_seq + 1 RETURNING last_seq

    单条 SQL 原子，无 race window。
    """

    __tablename__ = "promotion_sequence"

    date_key: Mapped[date] = mapped_column(Date, nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        Index(
            "uq_promotion_sequence",
            "tenant_id",
            "date_key",
            unique=True,
        ),
        CheckConstraint(
            "last_seq >= 0",
            name="ck_promotion_sequence_nonneg",
        ),
        CheckConstraint(
            "last_seq <= 9999",
            name="ck_promotion_sequence_max",
        ),
    )


class BloggerRetrospective(TenantScopedModel):
    """复盘文字，沉淀到博主档案（PRD V1.4 改动 4）。

    **为什么是独立子表而不是 promotion 上的一个 Text 字段**：PRD 原文「复盘文字永久
    写入博主档案（跨单据伴随这个博主）」「不随单据关闭而丢失」。存在 promotion 字段上
    的话，PR 被主管打回后重写会覆盖上一版，推广单软删后 hover 卡也查不到 —— 两条都
    不满足「永久」。

    一个推广单可以有多条：被打回重写时追加一条，旧的留着。「当前生效」的那条 =
    这个推广单下最新的一条。

    博主档案（hover 卡）只展示 ``confirmed_at IS NOT NULL`` 的 —— 没过主管的复盘
    是草稿，不该进档案误导下次选博主的人。
    """

    __tablename__ = "blogger_retrospective"

    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    promotion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """哪一单的复盘。``RESTRICT`` 而不是 ``SET NULL``：复盘脱离了单据就没有上下文，
    而推广单本来也只走软删（``publish_status='已删除'``），不会真删行。"""

    content: Mapped[str] = mapped_column(Text, nullable=False)
    """自由文本。PRD：数据表现、博主配合度、是否二搭、下次合作建议等，不拆结构化字段。"""

    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # hover 卡：按博主倒序取全部历史复盘
        Index(
            "idx_blogger_retro_blogger",
            "tenant_id",
            "blogger_id",
            text("created_at DESC"),
        ),
        # 单据详情：取这一单的复盘（含被打回的旧版本）
        Index("idx_blogger_retro_promotion", "tenant_id", "promotion_id"),
        CheckConstraint("length(btrim(content)) > 0", name="ck_blogger_retro_content_nonempty"),
        # 确认人与确认时间必须同时有或同时没有
        CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)",
            name="ck_blogger_retro_confirm_fields",
        ),
    )


__all__ = ["BloggerRetrospective", "Promotion", "PromotionSequence"]
