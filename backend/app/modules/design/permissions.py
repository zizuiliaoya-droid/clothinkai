"""U10a design 模块权限 scope 常量（module.sub:action）。"""

from __future__ import annotations

SCOPE_DESIGN_READ = "design.design:read"
SCOPE_DESIGN_WRITE = "design.design:write"
SCOPE_PATTERN_READ = "design.pattern:read"
SCOPE_PATTERN_WRITE = "design.pattern:write"
SCOPE_CRAFT_WRITE = "design.craft:write"
SCOPE_COSTING_WRITE = "design.costing:write"
SCOPE_TAG_PRICE_WRITE = "design.tag_price:write"
SCOPE_CONFIRM_PRICE = "design.confirm_price:approve"

#: migration 013 seed 的全部 design 细分 scope
ALL_DESIGN_SCOPES: tuple[str, ...] = (
    SCOPE_DESIGN_READ,
    SCOPE_DESIGN_WRITE,
    SCOPE_PATTERN_READ,
    SCOPE_PATTERN_WRITE,
    SCOPE_CRAFT_WRITE,
    SCOPE_COSTING_WRITE,
    SCOPE_TAG_PRICE_WRITE,
    SCOPE_CONFIRM_PRICE,
)


__all__ = [
    "ALL_DESIGN_SCOPES",
    "SCOPE_CONFIRM_PRICE",
    "SCOPE_COSTING_WRITE",
    "SCOPE_CRAFT_WRITE",
    "SCOPE_DESIGN_READ",
    "SCOPE_DESIGN_WRITE",
    "SCOPE_PATTERN_READ",
    "SCOPE_PATTERN_WRITE",
    "SCOPE_TAG_PRICE_WRITE",
]
