"""U07 wecom 模块业务异常。

继承 ``core/exceptions.py`` 的 AppException。错误码矩阵见 business-rules §10。
WecomApiError / WecomRateLimited / WecomTokenExpired 为外部调用内部异常
（不一定直接冒泡 HTTP，由 send_service 转 message.status）。
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppException


class WecomBloggerNoWechatError(AppException):
    code = "WECOM_BLOGGER_NO_WECHAT"
    status_code = 422
    message = "该博主未填写微信号"


class WecomContactNotFoundError(AppException):
    code = "WECOM_CONTACT_NOT_FOUND"
    status_code = 404
    message = "请先在企微端添加该联系人"


class WecomNotConfiguredError(AppException):
    code = "WECOM_NOT_CONFIGURED"
    status_code = 409
    message = "请先配置企微应用"


class WecomTemplateInvalidVarError(AppException):
    code = "WECOM_TEMPLATE_INVALID_VAR"
    status_code = 422
    message = "模板包含非法变量"

    def __init__(self, invalid: list[str]) -> None:
        super().__init__(
            "模板包含非法变量：" + ", ".join(invalid),
            details={"invalid": invalid},
        )


class WecomCallbackBadSignatureError(AppException):
    code = "WECOM_CALLBACK_BAD_SIGNATURE"
    status_code = 403
    message = "回调签名校验失败"


class WecomDecryptFailedError(AppException):
    code = "WECOM_DECRYPT_FAILED"
    status_code = 500
    message = "企微凭据解密失败"


class WecomApiError(AppException):
    """企微 API 返回 errcode≠0（非频控/非 token 失效）。"""

    code = "WECOM_API_ERROR"
    status_code = 502
    message = "企微接口调用失败"

    def __init__(self, errcode: Any, errmsg: str | None = None) -> None:
        super().__init__(
            f"企微接口错误 errcode={errcode}: {errmsg}",
            details={"errcode": errcode, "errmsg": errmsg},
        )
        self.errcode = errcode


class WecomRateLimited(Exception):
    """企微返回频控类错误码（send_service 转降级）。"""

    def __init__(self, errcode: Any = None, errmsg: str | None = None) -> None:
        super().__init__(f"wecom rate limited errcode={errcode}: {errmsg}")
        self.errcode = errcode


class WecomTokenExpired(Exception):
    """access_token 失效（40014/42001），client 内部刷新重试。"""


class AlertConfigInvalidError(AppException):
    code = "WECOM_ALERT_CONFIG_INVALID"
    status_code = 400
    message = "预警配置参数非法"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, details={"reason": reason})


__all__ = [
    "AlertConfigInvalidError",
    "WecomApiError",
    "WecomBloggerNoWechatError",
    "WecomCallbackBadSignatureError",
    "WecomContactNotFoundError",
    "WecomDecryptFailedError",
    "WecomNotConfiguredError",
    "WecomRateLimited",
    "WecomTemplateInvalidVarError",
    "WecomTokenExpired",
]
