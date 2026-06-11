"""U07 wecom 消息状态 + 回调结果映射单元测试。"""

from __future__ import annotations

from app.modules.wecom.callback_service import _RESULT_MAP
from app.modules.wecom.enums import (
    NotificationType,
    TemplateType,
    WecomMessageStatus,
)


def test_status_six_values():
    assert {s.value for s in WecomMessageStatus} == {
        "pending",
        "created",
        "sent",
        "rejected",
        "rate_limited",
        "failed",
    }


def test_template_types():
    assert {t.value for t in TemplateType} == {"urge", "urge_important"}


def test_notification_types():
    assert NotificationType.URGE_MANUAL.value == "urge_manual"
    assert NotificationType.URGE_UNBOUND.value == "urge_unbound"


def test_callback_result_map():
    assert _RESULT_MAP["success"] == "sent"
    assert _RESULT_MAP["reject"] == "rejected"
    assert _RESULT_MAP["fail"] == "failed"
