"""只读排查的固定 HTTP 范围；新增 GET 不会自动获得放行。"""

import re

SUPPORT_READ_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/users/",
        "/api/users/capabilities",
        "/api/permissions",
        "/api/users/{user_id}/effective-permissions",
        "/api/styles/",
        "/api/styles/match",
        "/api/styles/{style_id}",
        "/api/styles/{style_id}/goods",
        "/api/skus/",
        "/api/skus/by-style/{style_id}",
        "/api/skus/{sku_id}",
        "/api/brands/",
        "/api/brands/{brand_id}",
        "/api/dict-items",
        "/api/goods/",
        "/api/goods/brand-options",
        "/api/goods/season-options",
        "/api/goods/{goods_id}",
        "/api/platform-products/",
        "/api/platform-products/lookup",
        "/api/bloggers/",
        "/api/bloggers/{blogger_id}",
        "/api/blogger-tags",
        "/api/bloggers/{blogger_id}/retrospectives",
        "/api/negotiations/blogger/{blogger_id}/history",
        "/api/promotions/",
        "/api/promotions/stage-counts",
        "/api/promotions/filter-options",
        "/api/promotions/{promotion_id}",
        "/api/promotions/{promotion_id}/amount-log",
        "/api/return-freight-payments/",
        "/api/return-freight-payments/{freight_id}",
        "/api/settlements/",
        "/api/settlements/daily-summary/as-of",
        "/api/settlements/daily-summary/activity",
        "/api/settlements/{settlement_id}",
        "/api/lines/{line_id}/events",
        "/api/urge/promotions/{promotion_id}/records",
        "/api/settings/promotion-platforms",
        "/api/preferences/columns/promotion_list",
        "/api/preferences/filters/{page_code}",
        "/api/reports/publish-progress/summary",
        "/api/reports/publish-progress/cards",
        "/api/reports/publish-progress/styles/{style_id}/by-pr",
        "/api/reports/publish-progress/styles/{style_id}/by-time",
        "/api/reports/work-progress",
        "/api/reports/work-progress/goods",
        "/api/reports/work-progress/weeks",
        "/api/reports/targets",
        "/api/reports/store/daily",
        "/api/reports/store/weekly",
        "/api/reports/store/monthly",
        "/api/reports/store-daily",
        "/api/reports/production",
        "/api/reports/production/trend",
        "/api/reports/summaries/freshness",
        "/api/reports/season-options",
        "/api/reports/bi",
        "/api/reports/bi/personal",
        "/api/reports/bi/layout",
        "/api/reports/bi/goods/{goods_id}/trend",
        "/api/reports/bi/goods/{goods_id}/notes",
        "/api/reports/drill/promotions",
        "/api/reports/capabilities",
    }
)


def _pattern(path: str) -> re.Pattern[str]:
    # 路径参数只能占一个段；UUID 参数不接受 export/token 等固定功能名。
    def parameter(match: re.Match[str]) -> str:
        return r"[0-9a-fA-F-]{36}" if match.group(1).endswith("_id") else r"[a-zA-Z0-9_-]{1,64}"

    return re.compile(re.sub(r"\{([^}]+)\}", parameter, path.rstrip("/")) + "/?")


_SUPPORT_PATTERNS = tuple(_pattern(path) for path in SUPPORT_READ_PATHS)


def support_read_allowed(method: str, path: str) -> bool:
    return method == "GET" and any(pattern.fullmatch(path) for pattern in _SUPPORT_PATTERNS)


def platform_request_allowed(method: str, path: str) -> bool:
    path = path.rstrip("/")
    if (method, path) in {
        ("GET", "/api/auth/me"),
        ("PUT", "/api/auth/password"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/tenants"),
        ("POST", "/api/tenants"),
        ("POST", "/api/platform/support-sessions"),
    }:
        return True
    return (
        method == "PUT" and re.fullmatch(r"/api/tenants/[0-9a-fA-F-]{36}/status", path) is not None
    )
