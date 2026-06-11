"""U07 wecom 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class WecomMessageStatus(str, Enum):
    """wecom_message 状态机（6 状态，BR-U07-06）。

    - PENDING: 扫描已写入，待执行群发（起点）
    - CREATED: 已调 add_msg_template 创建群发，待 PR 企微端确认
    - SENT: 回调确认已发送（终态）
    - REJECTED: PR 在企微端拒绝（终态）
    - RATE_LIMITED: 频控降级，已转站内通知（终态）
    - FAILED: API 调用或发送失败（终态）
    """

    PENDING = "pending"
    CREATED = "created"
    SENT = "sent"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


class TemplateType(str, Enum):
    """催发模板类型（2 值；超时复用 urge_important）。"""

    URGE = "urge"
    URGE_IMPORTANT = "urge_important"


class NotificationType(str, Enum):
    """站内通知类型。"""

    URGE_MANUAL = "urge_manual"        # 频控降级：请手动催发
    URGE_UNBOUND = "urge_unbound"      # 博主未绑定企微
    SYSTEM = "system"                  # 系统通知（备份失败告警等）
    DESIGN_ADVANCE = "design_advance"  # U10a 设计制版推进到下一环节
    DESIGN_REJECT = "design_reject"    # U10a 设计制版驳回到上游
    DESIGN_DONE = "design_done"        # U10a 转大货通知设计师
    CREDENTIAL_FAILURE = "credential_failure"  # U12 凭据连续采集失败自动暂停告警


class AlertType(str, Enum):
    """U15 异常预警类型。"""

    RETURN_RATE_HIGH = "return_rate_high"   # 退货退款率过高
    ROI_LOW = "roi_low"                     # 净投产比过低
    CONVERSION_LOW = "conversion_low"       # 加购转化率过低（V1 占位）


__all__ = ["AlertType", "NotificationType", "TemplateType", "WecomMessageStatus"]
