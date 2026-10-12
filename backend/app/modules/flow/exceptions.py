"""流程线矩阵的两个错误码（流程线设计 7.1 错误码表），外加事件 payload 超限（6.3）。

- ``FLOW_ACTION_FORBIDDEN``（403）：端点 scope 够、service 规则（本人 / 自审 / 不持 ``*`` / ≠ 本轮主管……）不满足；
  ``details.rule`` ∈ ``FLOW_RULES``，``details.reason`` 与按钮悬停文案是同一份
- ``FLOW_GATE_MISSING``（422）：★ 卡点缺项；``details.missing`` 与 ``ui`` 里的缺项逐条相同

缺端点 scope 仍是现有的 ``PERMISSION_DENIED``，状态机不允许仍是 ``ILLEGAL_STATE_TRANSITION``。
时间线读接口看不到这条线：``LINE_NOT_FOUND``（404，6.5 第 1 层）。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.exceptions import AppException, PermissionDeniedError, ResourceNotFoundError

# 7.1 表里 FLOW_ACTION_FORBIDDEN 的 rule 取值（admin_only 是 N12 转交）
FLOW_RULES: tuple[str, ...] = (
    "not_owner",
    "self_review",
    "star_first_level",
    "same_reviewer",
    "star_create",
    "admin_only",
)


class FlowActionForbiddenError(PermissionDeniedError):
    code = "FLOW_ACTION_FORBIDDEN"
    status_code = 403
    message = "当前用户不能执行这个操作"

    def __init__(self, *, rule: str, reason: str) -> None:
        if rule not in FLOW_RULES:
            raise ValueError(f"未登记的流程规则: {rule!r}")
        super().__init__(reason, details={"rule": rule, "reason": reason})
        self.rule = rule
        self.reason = reason


class FlowGateMissingError(AppException):
    code = "FLOW_GATE_MISSING"
    status_code = 422
    message = "缺少必填项"

    def __init__(self, missing: Sequence[tuple[str, str]]) -> None:
        """``missing``：``(key, label)`` 列表，顺序与矩阵里 Gate 的顺序一致。"""
        if not missing:
            raise ValueError("FlowGateMissingError 至少要有一个缺项")
        items = [{"key": key, "label": label} for key, label in missing]
        super().__init__(
            "缺：" + "、".join(label for _, label in missing),
            details={"missing": items},
        )
        self.missing = tuple(missing)


class LineNotFoundError(ResourceNotFoundError):
    """时间线：这条线不存在，或当前用户看不到线上的任何单据（6.5 第 1 层，回 404 不回 403，免得暴露单据存在）。"""

    code = "LINE_NOT_FOUND"
    status_code = 404
    message = "记录不存在"


class LineEventPayloadTooLargeError(AppException):
    """事件 payload 超过单条上限（设计 6.3「单条上限 8 KB」）：整个动作跟着失败（6.4，不吞异常）。"""

    code = "LINE_EVENT_PAYLOAD_TOO_LARGE"
    status_code = 422
    message = "时间线记录内容过长"

    def __init__(self, *, event_type: str, size: int, limit: int) -> None:
        super().__init__(
            f"时间线记录内容过长（{size} 字节，上限 {limit}）",
            details={"event_type": event_type, "size": size, "limit": limit},
        )
