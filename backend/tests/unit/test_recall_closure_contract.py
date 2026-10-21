"""召回公开请求合同，验证API实际声明，真实事务另在PG18集成中验证。"""

import pytest

pytestmark = pytest.mark.unit


def test_success_api_requires_freight_amount_and_payer():
    from app.main import app

    schema = app.openapi()
    body = schema["paths"]["/api/promotions/{promotion_id}/recall/success"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    fields = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    assert {"return_shipping_fee", "return_shipping_payer"}.issubset(fields.get("required", []))
