"""FIELD_PERMISSION_DENIED 带全部越权字段（流程线设计 7.1）。

``details.fields`` 列出全部越权字段；``details.field`` 保留为第一个，兼容旧断言与旧前端。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import FieldPermissionDenied, PermissionDeniedError


def test_single_field_keeps_old_shape_and_adds_fields() -> None:
    exc = FieldPermissionDenied(field="quote_amount", entity="promotion")
    assert exc.code == "FIELD_PERMISSION_DENIED"
    assert exc.status_code == 403
    assert exc.field == "quote_amount"
    assert exc.entity == "promotion"
    assert exc.fields == ("quote_amount",)
    assert exc.details == {
        "field": "quote_amount",
        "fields": ["quote_amount"],
        "entity": "promotion",
    }
    assert isinstance(exc, PermissionDeniedError)


def test_single_field_without_entity() -> None:
    exc = FieldPermissionDenied(field="wechat")
    assert exc.details == {"field": "wechat", "fields": ["wechat"]}
    assert exc.entity is None


def test_multiple_fields_first_is_field() -> None:
    exc = FieldPermissionDenied(fields=["quote", "wechat", "phone"], entity="blogger")
    assert exc.code == "FIELD_PERMISSION_DENIED"
    assert exc.field == "quote"
    assert exc.fields == ("quote", "wechat", "phone")
    assert exc.details["field"] == "quote"
    assert exc.details["fields"] == ["quote", "wechat", "phone"]
    assert exc.details["entity"] == "blogger"
    assert "quote" in exc.message and "phone" in exc.message


def test_empty_is_programming_error() -> None:
    with pytest.raises(ValueError):
        FieldPermissionDenied()
    with pytest.raises(ValueError):
        FieldPermissionDenied(fields=[])
