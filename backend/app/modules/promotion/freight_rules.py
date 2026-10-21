"""R33状态合同与召回精确例外；不包含数据库或角色名称判断。"""

from app.core.exceptions import AppException, ResourceNotFoundError, ValidationError


class FreightStateConflictError(AppException):
    code = "FREIGHT_STATE_CONFLICT"
    status_code = 409
    message = "寄回运费单状态已变更，请刷新后重试"


class FreightNotFoundError(ResourceNotFoundError):
    code = "FREIGHT_NOT_FOUND"


class FreightValidationError(ValidationError):
    code = "FREIGHT_REQUIREMENTS_MISSING"


TRANSITIONS = {
    ("待审核", "approve"): "待付款",
    ("待审核", "reject"): "已驳回",
    ("待付款", "pay"): "已付款",
    ("待付款", "reject_payment"): "已驳回",
    ("已驳回", "resubmit"): "待审核",
}


def freight_transition(status: str, action: str) -> str:
    target = TRANSITIONS.get((status, action))
    if target is None:
        raise FreightStateConflictError(details={"status": status, "action": action})
    return target


def continuing_published_recall(
    *, publish_status: str, recall_status: str, is_active: bool
) -> bool:
    """原已在进行中的已发布召回允许跨正常完结继续C3/C4/C5，不允许新发起。"""
    return is_active and publish_status == "已发布" and recall_status == "召回中"
