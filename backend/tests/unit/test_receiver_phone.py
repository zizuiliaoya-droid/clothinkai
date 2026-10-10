"""收件电话校验与规范化（流程线 7.1；推广单 PATCH / POST / 推送仓库与谈款 N9 共用一个函数）。

规则：BTRIM 后空串 = 清空（None）；去掉空白与短横后，11 位手机号（1 开头，可带 +86 / 86，存时去前缀）
或 0 开头的 10 ~ 12 位座机；其余 422 ``INVALID_RECEIVER_PHONE``。
"""

from __future__ import annotations

import pytest

from app.modules.promotion.exceptions import InvalidReceiverPhoneError
from app.modules.promotion.receiver import normalize_receiver_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13812345678", "13812345678"),
        ("138 1234 5678", "13812345678"),
        ("138-1234-5678", "13812345678"),
        ("  13812345678  ", "13812345678"),
        ("+8613812345678", "13812345678"),
        ("+86 138 1234 5678", "13812345678"),
        ("8613812345678", "13812345678"),
        ("86-138-1234-5678", "13812345678"),
        # 座机：0 开头 10 ~ 12 位
        ("0571-8888888", "05718888888"),
        ("010 12345678", "01012345678"),
        ("0571888888", "0571888888"),
        ("057188888888", "057188888888"),
        # 全角空格也算空白
        ("138\u30001234\u30005678", "13812345678"),
    ],
)
def test_valid_phone_is_normalized(raw: str, expected: str) -> None:
    assert normalize_receiver_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\u3000"])
def test_blank_means_clear(raw: str | None) -> None:
    assert normalize_receiver_phone(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "1381234567",  # 10 位手机
        "138123456789",  # 12 位手机
        "23812345678",  # 11 位但不是 1 开头、也不是 0 开头
        "057188888",  # 座机 9 位
        "0571888888888",  # 座机 13 位
        "+86057188888888",  # +86 只认手机
        "+8513812345678",  # 别的国家码
        "138.1234.5678",  # 只去空白与短横
        "138(1234)5678",
        "电话13812345678",
        "-",
        "abc",
    ],
)
def test_invalid_phone_raises(raw: str) -> None:
    with pytest.raises(InvalidReceiverPhoneError) as exc_info:
        normalize_receiver_phone(raw)
    assert exc_info.value.code == "INVALID_RECEIVER_PHONE"
    assert exc_info.value.status_code == 422
    assert exc_info.value.details == {"field": "receiver_phone"}
