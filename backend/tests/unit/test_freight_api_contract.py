"""七端点实际FastAPI合同，不连DB；缺路由以公开API能力断言失败。"""

import pytest

pytestmark = pytest.mark.unit


def test_freight_seven_endpoints_have_exact_public_contract():
    from app.main import app

    paths = app.openapi()["paths"]
    expected = {
        "/api/promotions/{promotion_id}/return-freight": "post",
        "/api/return-freight-payments/": "get",
        "/api/return-freight-payments/{freight_id}": "get",
        "/api/return-freight-payments/{freight_id}/review": "post",
        "/api/return-freight-payments/{freight_id}/pay": "post",
        "/api/return-freight-payments/{freight_id}/reject-payment": "post",
        "/api/return-freight-payments/{freight_id}/resubmit": "post",
    }
    assert all(path in paths and method in paths[path] for path, method in expected.items())
    for path, method in expected.items():
        assert paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "delete" not in paths[path] and "patch" not in paths[path]
