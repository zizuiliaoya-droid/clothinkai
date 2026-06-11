"""单元测试：BR-PWD-001 密码策略校验 + BR-PWD-004 临时密码生成。"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.modules.auth.domain import generate_random_password
from app.modules.auth.exceptions import WeakPasswordError
from app.modules.auth.schemas import ChangePasswordRequest


@pytest.mark.unit
class TestPasswordStrengthValidation:
    def test_valid_strong_password(self) -> None:
        # 满足：≥10 字符 + 大写 + 小写 + 数字
        req = ChangePasswordRequest(old_password="anyOld1", new_password="GoodPass123")
        assert req.new_password == "GoodPass123"

    def test_too_short(self) -> None:
        with pytest.raises(WeakPasswordError) as exc:
            ChangePasswordRequest(old_password="x", new_password="Short1A")  # < 10
        assert "10" in str(exc.value.message)

    def test_no_uppercase(self) -> None:
        with pytest.raises(WeakPasswordError):
            ChangePasswordRequest(old_password="x", new_password="alllower123")

    def test_no_lowercase(self) -> None:
        with pytest.raises(WeakPasswordError):
            ChangePasswordRequest(old_password="x", new_password="ALLUPPER123")

    def test_no_digit(self) -> None:
        with pytest.raises(WeakPasswordError):
            ChangePasswordRequest(old_password="x", new_password="NoDigitsHere")


@pytest.mark.unit
class TestGenerateRandomPassword:
    def test_default_length(self) -> None:
        p = generate_random_password()
        assert len(p) == 16

    def test_minimum_length_enforced(self) -> None:
        # 即使传 5，也应当至少返回 10 字符（满足 BR-PWD-001）
        p = generate_random_password(5)
        assert len(p) == 10

    def test_contains_all_required_classes(self) -> None:
        for _ in range(20):
            p = generate_random_password(16)
            assert re.search(r"[A-Z]", p), f"missing uppercase in {p}"
            assert re.search(r"[a-z]", p), f"missing lowercase in {p}"
            assert re.search(r"\d", p), f"missing digit in {p}"
            assert re.search(r"[!@#$%^&*\-_=+]", p), f"missing special in {p}"

    def test_passes_change_password_validator(self) -> None:
        """生成的临时密码必须能通过 ChangePasswordRequest 校验。"""
        for _ in range(20):
            p = generate_random_password(16)
            req = ChangePasswordRequest(old_password="x", new_password=p)
            assert req.new_password == p

    def test_randomness(self) -> None:
        passwords = {generate_random_password() for _ in range(50)}
        assert len(passwords) == 50  # 不重复
