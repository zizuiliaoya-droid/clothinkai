"""催发任务 ORM（PRD V1.4 改动 2）。

三张表的分工见 migration 049 的模块注释。这里只强调一点：``urge_task`` 是**一个推广单
一个任务**，而 U07 的 ``wecom_message`` 是按 (blogger, pr) 聚合的、一条消息覆盖多个
推广单。PRD 要的「博主确认发布 → 任务自动关闭」「超过 N 次提示主管」都是单据维度的
语义，塞不进聚合消息里，所以另建而不是复用。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class UrgeConfig(TenantScopedModel):
    """催发阈值（单租户单行）。

    顺便收编了 ``promotion/legacy_settings.py`` 里的 ``URGE_THRESHOLD_DAYS`` 与
    ``IMPORTANT_THRESHOLD_DAYS`` —— 那两个常量在 ``wecom/scan_service.py`` 还被
    重复定义了一遍（``_URGE_DAYS`` / ``_IMPORTANT_DAYS``），双份真相谁改一边就不一致。
    """

    __tablename__ = "urge_config"

    no_publish_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    """距预定发布日还剩多少天就开始自动催（PRD 默认 5 天）。"""

    max_urge_times: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    """催过多少次之后提示主管考虑召回 / 转取消（PRD 默认 3 次）。

    注意这是**提示**阈值，不是硬上限 —— 超了还能继续催，只是列表会标记出来。
    """

    max_overdue_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )
    """超时超过这么多天就不再自动建任务。

    不是 PRD 要求的，是防炸的：``find_urge_candidates`` 把 urge_status='超时' 也算
    候选，而生产有 5134 条历史单的排期在半年前（diff -113 ~ -184 天）。没有这个上限，
    自动扫描第一次跑就建 5134 个任务，之后天天催。这些陈旧单仍可手动催。
    """

    urge_threshold_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
    """urge_status 的「档期内 / 催发」分界（原 legacy_settings.URGE_THRESHOLD_DAYS）。"""

    important_threshold_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    """urge_status 的「催发 / 重要催发」分界（原 IMPORTANT_THRESHOLD_DAYS）。"""

    auto_scan_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    """关掉就只能手动催。排查问题或业务暂停时用，比去改 Beat 配置方便。"""

    __table_args__ = (
        Index("uq_urge_config_tenant", "tenant_id", unique=True),
        CheckConstraint("no_publish_days BETWEEN 1 AND 60", name="ck_urge_config_no_publish_days"),
        CheckConstraint("max_urge_times BETWEEN 1 AND 20", name="ck_urge_config_max_urge_times"),
        CheckConstraint(
            "max_overdue_days BETWEEN 1 AND 365", name="ck_urge_config_max_overdue_days"
        ),
        CheckConstraint(
            "urge_threshold_days BETWEEN 1 AND 60", name="ck_urge_config_urge_threshold_days"
        ),
        CheckConstraint(
            "important_threshold_days >= 0 AND important_threshold_days <= urge_threshold_days",
            name="ck_urge_config_important_le_urge",
        ),
    )


class UrgeTask(TenantScopedModel):
    """一个推广单一个催发任务。"""

    __tablename__ = "urge_task"

    promotion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="RESTRICT"),
        nullable=False,
    )

    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """冗余列：列表要显示博主昵称，存一份省掉每次 join promotion。

    推广单的 blogger_id 建单后不会变，不存在漂移风险。
    """

    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """冗余列：主管看板按 PR 聚合。"""

    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'进行中'"))
    urge_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_urged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_auto_urged_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    """最后一次**自动**催发的日期（租户时区）。

    自动扫描的当日幂等靠它：``UPDATE ... WHERE last_auto_urged_on IS DISTINCT FROM
    :today`` 单语句原子，0 行就跳过。比表达式唯一索引简单，也比
    ``wecom/scan_service`` 的 SELECT-then-INSERT 可靠（那个真并发会双发）。
    """

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        # 一单一任务。自动扫描靠这个唯一约束幂等（ON CONFLICT DO NOTHING）
        Index("uq_urge_task_promotion", "tenant_id", "promotion_id", unique=True),
        Index("idx_urge_task_status", "tenant_id", "status", "last_urged_at"),
        Index("idx_urge_task_pr", "tenant_id", "pr_id", "status"),
        Index("idx_urge_task_blogger", "tenant_id", "blogger_id"),
        CheckConstraint("status IN ('进行中', '已关闭')", name="ck_urge_task_status"),
        CheckConstraint("urge_count >= 0", name="ck_urge_task_urge_count_nonneg"),
        CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('博主已发布', '已取消', '手动关闭')",
            name="ck_urge_task_close_reason",
        ),
        # 关闭必须留时间与原因，没关闭就不该有 —— 否则看板的「进行中」会算错
        CheckConstraint(
            "(status = '已关闭' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)"
            " OR (status = '进行中' AND closed_at IS NULL AND close_reason IS NULL)",
            name="ck_urge_task_closed_fields",
        ),
    )


class UrgeRecord(TenantScopedModel):
    """一次催发的留痕。一个任务多条，永久保留（无 is_active）。"""

    __tablename__ = "urge_record"

    urge_task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("urge_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    promotion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """冗余列：单据详情页要直接按推广单取全部催发记录，不想先查任务。"""

    trigger_type: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    screenshot_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """催发截图。手动催发可传，自动催发没有，所以可空。

    ``RESTRICT``：截图是留痕证据，不允许附件先被删掉留下空引用。
    一条留痕一张图 —— 现有 4 处图片上传全是单张 FK 模式，没有多张的可抄实现，
    真需要贴多张再加子表。
    """

    wecom_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("wecom_message.id", ondelete="SET NULL"),
        nullable=True,
    )
    """企微可用时关联投递出去的那条消息；不可用时为空，留痕照样成立。"""

    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # 时间线倒序（详情页）
        Index("idx_urge_record_task", "tenant_id", "urge_task_id", text("created_at DESC")),
        # 看板「本周已催发 N」
        Index("idx_urge_record_created", "tenant_id", "created_at"),
        Index("idx_urge_record_promotion", "tenant_id", "promotion_id"),
        CheckConstraint("trigger_type IN ('手动', '自动')", name="ck_urge_record_trigger_type"),
    )


__all__ = ["UrgeConfig", "UrgeRecord", "UrgeTask"]
